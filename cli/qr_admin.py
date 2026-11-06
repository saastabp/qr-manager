"""qr-admin: command-line client for the QR Manager API.

A thin client. It signs in, calls the API, shows the result, and saves PNGs. Every business
decision stays on the server, as it will for the Phase II UI. See docs/DESIGN.md, section 8.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import boto3
import requests
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger("qr-admin")

REGION = "us-east-1"
OUTPUTS_DIR = Path(__file__).resolve().parents[1] / "infra"
TOKEN_DIR = Path.home() / ".config" / "qr-manager"

#: Refresh a token this many seconds before it expires.
EXPIRY_MARGIN_SECONDS = 60

#: Longer than a cold create, which was measured at under 6 seconds.
REQUEST_TIMEOUT_SECONDS = 35

#: The PNG saved when no logo is chosen. The API decides which logos exist.
DEFAULT_LOGO = "full"


class CliError(Exception):
    """A problem to show the user as a message, without a traceback."""


@dataclass(frozen=True)
class Settings:
    """Where one environment's API and sign-in live.

    Parameters
    ----------
    env : str
        The environment name.
    api_url : str
        The API's base URL.
    client_id : str
        The Cognito client id.
    """

    env: str
    api_url: str
    client_id: str


def load_settings(env: str, outputs_dir: Path | None = None) -> Settings:
    """Read an environment's settings from its CDK outputs file.

    Parameters
    ----------
    env : str
        The environment name.
    outputs_dir : Path or None
        The folder holding the outputs file. Defaults to ``infra/``.

    Returns
    -------
    Settings
        The environment's settings.

    Raises
    ------
    CliError
        If the outputs file is missing, corrupt, or incomplete.
    """
    path = (outputs_dir or OUTPUTS_DIR) / f"outputs-{env}.json"
    if not path.is_file():
        raise CliError(
            f"{path} not found. Deploy with --outputs-file outputs-{env}.json to create it."
        )

    try:
        stacks = json.loads(path.read_text())
    except ValueError as error:
        raise CliError(f"{path} is not valid JSON: {error}") from error

    api = stacks.get(f"qr-manager-{env}-Api", {})
    auth = stacks.get(f"qr-manager-{env}-Auth", {})
    api_url = next((value for key, value in api.items() if "ApiUrl" in key), None)
    client_id = auth.get("CliClientId")
    if not api_url or not client_id:
        raise CliError(f"{path} is missing the Api or Auth outputs.")

    return Settings(env=env, api_url=api_url, client_id=client_id)


class Session:
    """Holds sign-in tokens for one environment.

    Parameters
    ----------
    settings : Settings
        The environment.
    token_dir : Path
        Where tokens are cached.
    cognito : Any or None
        A Cognito client. Created if omitted. Sign-in needs no AWS credentials.
    """

    def __init__(
        self, settings: Settings, token_dir: Path = TOKEN_DIR, cognito: Any | None = None
    ) -> None:
        self._settings = settings
        self._path = token_dir / f"tokens-{settings.env}.json"
        self._cognito = cognito or boto3.client(
            "cognito-idp", region_name=REGION, config=Config(signature_version=UNSIGNED)
        )

    def login(self, email: str, password: str) -> None:
        """Sign in and cache the tokens.

        Parameters
        ----------
        email : str
            The account's email address.
        password : str
            The account's password. It is not stored.

        Raises
        ------
        CliError
            If sign-in fails.
        """
        result = self._authenticate(
            "USER_PASSWORD_AUTH", {"USERNAME": email, "PASSWORD": password}
        )
        self._save(result, result["RefreshToken"])

    def id_token(self) -> str:
        """Return a current ID token, refreshing it if needed.

        Returns
        -------
        str
            The token.

        Raises
        ------
        CliError
            If the user needs to sign in again.
        """
        if not self._path.is_file():
            raise CliError(f"Not signed in. Run: qr-admin --env {self._settings.env} login")

        tokens = json.loads(self._path.read_text())
        if time.time() < tokens["expires_at"] - EXPIRY_MARGIN_SECONDS:
            return tokens["id_token"]

        result = self._authenticate(
            "REFRESH_TOKEN_AUTH", {"REFRESH_TOKEN": tokens["refresh_token"]}
        )
        self._save(result, tokens["refresh_token"])
        return result["IdToken"]

    def _authenticate(self, flow: str, parameters: dict[str, str]) -> dict[str, Any]:
        """Call Cognito and return its authentication result."""
        try:
            response = self._cognito.initiate_auth(
                ClientId=self._settings.client_id, AuthFlow=flow, AuthParameters=parameters
            )
        except (BotoCoreError, ClientError) as error:
            logger.debug("Sign-in failed", exc_info=True)
            raise CliError(f"Sign-in failed: {error}") from error

        if "AuthenticationResult" not in response:
            challenge = response.get("ChallengeName")
            raise CliError(f"Sign-in needs a step this script cannot do: {challenge}")

        return response["AuthenticationResult"]

    def _save(self, result: dict[str, Any], refresh_token: str) -> None:
        """Cache tokens in a file only this user can read."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.touch(mode=0o600, exist_ok=True)
        self._path.chmod(0o600)
        self._path.write_text(
            json.dumps(
                {
                    "id_token": result["IdToken"],
                    "refresh_token": refresh_token,
                    "expires_at": time.time() + result["ExpiresIn"],
                }
            )
        )


class ApiClient:
    """Calls the QR Manager API.

    Parameters
    ----------
    settings : Settings
        The environment.
    session : Session
        Supplies the sign-in token.
    """

    def __init__(self, settings: Settings, session: Session) -> None:
        self._settings = settings
        self._session = session

    def call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send a request and return the parsed response.

        Parameters
        ----------
        method : str
            The HTTP method.
        path : str
            The path, such as ``/codes``.
        body : dict[str, Any] or None
            The JSON body, if any.
        params : dict[str, str] or None
            The query string values, if any.

        Returns
        -------
        dict[str, Any]
            The response body.

        Raises
        ------
        CliError
            If the request fails, with the server's message.
        """
        correlation_id = str(uuid.uuid4())
        logger.debug("%s %s correlation_id=%s", method, path, correlation_id)
        try:
            response = requests.request(
                method,
                f"{self._settings.api_url}{path}",
                json=body,
                params=params,
                headers={
                    "authorization": f"Bearer {self._session.id_token()}",
                    "x-correlation-id": correlation_id,
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            raise CliError(f"Could not reach the API: {error}") from error

        logger.debug("status=%s correlation_id=%s", response.status_code, correlation_id)
        if response.ok:
            return response.json()

        raise CliError(_error_message(response, correlation_id))


def _error_message(response: requests.Response, correlation_id: str) -> str:
    """Build a readable message from an API error response."""
    try:
        error = response.json().get("error", {})
    except ValueError:
        error = {}

    lines = [error.get("message") or f"Request failed with status {response.status_code}"]
    for field in error.get("fields", []):
        lines.append(f"  {field['field']}: {field['message']}")
    lines.append(f"  correlation id: {correlation_id}")
    return "\n".join(lines)


def _show(code: dict[str, Any]) -> None:
    """Print one code."""
    print(f"{code['code']}  [{code['kind']}, {code['status']}]")
    print(f"  encodes:     {code['encoded_url']}")
    print(f"  destination: {code['destination']}")
    print(f"  final URL:   {code['final_url']}")
    if code["label"]:
        print(f"  label:       {code['label']}")
    if code["campaign"]:
        print(f"  campaign:    {code['campaign']}")


def _code_path(code: str) -> str:
    """Build the API path of a code. The name is typed by the user, so it is encoded."""
    return f"/codes/{quote(code, safe='')}"


def _save_png(api: ApiClient, code: str, logo: str, output: Path | None) -> None:
    """Download a code's PNG."""
    target = output or Path(f"{code}-{logo}.png")
    link = api.call("GET", f"{_code_path(code)}/image", params={"logo": logo})
    try:
        response = requests.get(link["url"], timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as error:
        raise CliError(f"Could not download the PNG: {error}") from error

    target.write_bytes(response.content)
    print(f"Saved {target}")


def _run_login(args: argparse.Namespace, session: Session, _api: ApiClient) -> None:
    """Handle the login command."""
    email = args.email or input("Email: ")
    session.login(email, getpass.getpass("Password: "))
    print(f"Signed in to {args.env}")


def _run_create(args: argparse.Namespace, _session: Session, api: ApiClient) -> None:
    """Handle the create command."""
    code = api.call(
        "POST",
        "/codes",
        {
            "code": args.code,
            "kind": args.kind,
            "destination": args.dest,
            "label": args.label,
            "campaign": args.campaign,
        },
    )
    _show(code)
    _save_png(api, args.code, DEFAULT_LOGO, args.output)


def _run_update(args: argparse.Namespace, _session: Session, api: ApiClient) -> None:
    """Handle the update command. Options left out are not sent."""
    fields = {"destination": args.dest, "label": args.label, "campaign": args.campaign}
    body = {name: value for name, value in fields.items() if value is not None}
    _show(api.call("PATCH", _code_path(args.code), body))


def _run_list(_args: argparse.Namespace, _session: Session, api: ApiClient) -> None:
    """Handle the list command."""
    for code in api.call("GET", "/codes")["codes"]:
        print(f"{code['code']:<32} {code['kind']:<8} {code['status']:<8} {code['destination']}")


def _run_show(args: argparse.Namespace, _session: Session, api: ApiClient) -> None:
    """Handle the show command."""
    _show(api.call("GET", _code_path(args.code)))


def _run_png(args: argparse.Namespace, _session: Session, api: ApiClient) -> None:
    """Handle the png command."""
    _save_png(api, args.code, args.logo, args.output)


def _run_resync(args: argparse.Namespace, _session: Session, api: ApiClient) -> None:
    """Handle the resync command. One failed code does not stop the others."""
    codes = api.call("GET", "/codes")["codes"]
    chosen = [code for code in codes if args.all or code["status"] == "pending"]
    failed = 0
    for code in chosen:
        try:
            result = api.call("PATCH", _code_path(code["code"]), {})
        except CliError as error:
            failed += 1
            print(f"{code['code']}: failed\n{error}", file=sys.stderr)
            continue

        print(f"{result['code']}: {result['status']}")

    print(f"Resynced {len(chosen) - failed} of {len(codes)} codes")
    if failed:
        raise CliError(f"{failed} of {len(chosen)} codes could not be resynced")


def build_parser() -> argparse.ArgumentParser:
    """Define the command line.

    Returns
    -------
    argparse.ArgumentParser
        The configured parser.
    """
    parser = argparse.ArgumentParser(prog="qr-admin", description=__doc__.splitlines()[0])
    parser.add_argument("--env", choices=["sandbox", "prod"], required=True)
    parser.add_argument("--verbose", action="store_true", help="show diagnostic logging")
    commands = parser.add_subparsers(dest="command", required=True)

    login = commands.add_parser("login", help="sign in")
    login.add_argument("--email")
    login.set_defaults(run=_run_login)

    create = commands.add_parser("create", help="create a code and save its PNG")
    create.add_argument("code")
    create.add_argument("--kind", choices=["dynamic", "static"], default="dynamic")
    create.add_argument("--dest", required=True, help="destination URL")
    create.add_argument("--campaign", default="", help="optional; blank means no UTMs")
    create.add_argument("--label", default="")
    create.add_argument("--output", type=Path, help="where to save the PNG")
    create.set_defaults(run=_run_create)

    update = commands.add_parser("update", help="change a code")
    update.add_argument("code")
    update.add_argument("--dest", help="new destination URL")
    update.add_argument("--campaign", help="new campaign; empty removes it")
    update.add_argument("--label")
    update.set_defaults(run=_run_update)

    commands.add_parser("list", help="list your codes").set_defaults(run=_run_list)

    show = commands.add_parser("show", help="show one code")
    show.add_argument("code")
    show.set_defaults(run=_run_show)

    png = commands.add_parser("png", help="download a code's PNG")
    png.add_argument("code")
    png.add_argument("--logo", default=DEFAULT_LOGO, help="full or flower")
    png.add_argument("--output", type=Path, help="where to save the PNG")
    png.set_defaults(run=_run_png)

    resync = commands.add_parser("resync", help="finish pending codes")
    resync.add_argument("--all", action="store_true", help="republish every code")
    resync.set_defaults(run=_run_resync)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command line.

    Parameters
    ----------
    argv : list[str] or None
        The arguments. Defaults to the process arguments.

    Returns
    -------
    int
        The exit code: 0 for success, 1 for an error.
    """
    args = build_parser().parse_args(argv)
    # Only this script's logger goes to debug. Debug on the root logger would also turn on
    # botocore's, which prints request bodies, including the password at sign-in.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    try:
        settings = load_settings(args.env)
        session = Session(settings)
        args.run(args, session, ApiClient(settings, session))
    except CliError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())