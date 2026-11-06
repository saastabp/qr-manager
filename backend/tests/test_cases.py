"""Tests for the business cases, using in-memory fakes. See docs/DESIGN.md, section 3."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fakes import OWNER, REDIRECT_DOMAIN, STRANGER, FakeWorld

from cases.change_code import ChangeCodeRequest, change_code
from cases.create_code import CreateCodeRequest, create_code
from cases.errors import (
    CodeConflict,
    CodeNotFound,
    CodeNotReady,
    CodeTaken,
    InvalidInput,
    StepFailed,
)
from cases.get_image import get_image
from cases.policies import MAX_LABEL_CHARACTERS, allowed_actions, status_of
from cases.redirect_url import build_final_url
from cases.view_codes import get_code, list_codes
from common.logger import logger
from models.qr_code import CODE_PATTERN, CodeKind, CodeStatus

DESTINATION = "https://example.com/page"
NEW_DESTINATION = "https://example.com/new"
REPO_ROOT = Path(__file__).parents[2]


@pytest.fixture
def world() -> FakeWorld:
    """Provide a fresh set of fakes for each test."""
    return FakeWorld()


def dynamic(code: str = "bldc-flyer", campaign: str = "bldc") -> CreateCodeRequest:
    """Build a request for a dynamic code."""
    return CreateCodeRequest(code, CodeKind.DYNAMIC, DESTINATION, campaign=campaign)


def static(code: str = "menu-card") -> CreateCodeRequest:
    """Build a request for a static code."""
    return CreateCodeRequest(code, CodeKind.STATIC, DESTINATION)


class TestCreate:
    """BC1."""

    def test_dynamic_code_is_published_rendered_and_live(self, world):
        code, created = create_code(dynamic(), OWNER, world.deps)

        assert created
        assert status_of(code) == CodeStatus.LIVE
        assert world.redirects.published["bldc-flyer"] == code.final_url
        assert world.renderer.calls == [
            ("bldc-flyer", f"https://{REDIRECT_DOMAIN}/bldc-flyer", "full")
        ]

    def test_static_code_is_rendered_but_never_published(self, world):
        code, _ = create_code(static(), OWNER, world.deps)

        assert status_of(code) == CodeStatus.STATIC
        assert world.redirects.calls == 0
        assert world.renderer.calls == [("menu-card", DESTINATION, "full")]

    def test_caller_becomes_owner(self, world):
        code, _ = create_code(dynamic(), OWNER, world.deps)

        assert code.owner_sub == OWNER.sub
        assert code.created_by == OWNER.email

    @pytest.mark.parametrize("name", ["api", "admin", "health"])
    def test_reserved_names_are_refused(self, world, name):
        with pytest.raises(InvalidInput):
            create_code(dynamic(name), OWNER, world.deps)

    @pytest.mark.parametrize("name", ["", "UPPER", "-bad", "bad-", "a.b", "a" * 65])
    def test_badly_formed_names_are_refused(self, world, name):
        with pytest.raises(InvalidInput) as caught:
            create_code(dynamic(name), OWNER, world.deps)

        assert caught.value.errors[0].field == "code"

    @pytest.mark.parametrize("url", ["http://example.com", "example.com", "https://", ""])
    def test_destination_must_be_full_https(self, world, url):
        with pytest.raises(InvalidInput) as caught:
            create_code(CreateCodeRequest("ok", CodeKind.DYNAMIC, url), OWNER, world.deps)

        assert caught.value.errors[0].field == "destination"

    @pytest.mark.parametrize(
        "url",
        [
            "https://exa\nmple.com/a",
            "https://example.com/a\tb",
            "https://example.com/a b",
            'https://example.com/"',
            "https://example.com/\\",
            "https://example.com/日本語",
            "https://[bad",
            "https://:443/",
        ],
    )
    def test_destination_must_be_a_safe_encoded_url(self, world, url):
        with pytest.raises(InvalidInput) as caught:
            create_code(CreateCodeRequest("ok", CodeKind.DYNAMIC, url), OWNER, world.deps)

        assert caught.value.errors[0].field == "destination"

    def test_a_label_can_be_the_longest_allowed(self, world):
        request = CreateCodeRequest(
            "ok", CodeKind.DYNAMIC, DESTINATION, label="a" * MAX_LABEL_CHARACTERS
        )

        code, _ = create_code(request, OWNER, world.deps)

        assert len(code.label) == MAX_LABEL_CHARACTERS

    def test_a_longer_label_is_refused(self, world):
        request = CreateCodeRequest(
            "ok", CodeKind.DYNAMIC, DESTINATION, label="a" * (MAX_LABEL_CHARACTERS + 1)
        )

        with pytest.raises(InvalidInput) as caught:
            create_code(request, OWNER, world.deps)

        assert caught.value.errors[0].field == "label"

    def test_a_percent_encoded_destination_is_allowed(self, world):
        url = "https://example.com/caf%C3%A9?a=1&b=%20#top"

        code, _ = create_code(CreateCodeRequest("ok", CodeKind.DYNAMIC, url), OWNER, world.deps)

        assert code.final_url == url

    def test_final_url_must_fit_in_the_redirect_store(self, world):
        long_url = f"https://example.com/{'a' * 1100}"

        with pytest.raises(InvalidInput):
            create_code(CreateCodeRequest("ok", CodeKind.DYNAMIC, long_url), OWNER, world.deps)

    def test_final_url_limit_is_900_bytes(self, world):
        prefix = "https://example.com/"
        longest = f"{prefix}{'a' * (900 - len(prefix))}"

        create_code(CreateCodeRequest("fits", CodeKind.DYNAMIC, longest), OWNER, world.deps)

        with pytest.raises(InvalidInput):
            too_long = CreateCodeRequest("over", CodeKind.DYNAMIC, f"{longest}a")
            create_code(too_long, OWNER, world.deps)

    @pytest.mark.parametrize(
        "campaign", ["Spring", "spring 2027", "a:b", "-bad", "bad-", "a" * 65]
    )
    def test_badly_formed_campaigns_are_refused(self, world, campaign):
        with pytest.raises(InvalidInput) as caught:
            create_code(dynamic(campaign=campaign), OWNER, world.deps)

        assert caught.value.errors[0].field == "campaign"

    def test_a_blank_campaign_is_allowed(self, world):
        code, _ = create_code(dynamic(campaign=""), OWNER, world.deps)

        assert code.campaign == ""

    def test_the_campaign_is_published_with_the_code(self, world):
        create_code(dynamic(campaign="spring-2027"), OWNER, world.deps)

        assert world.redirects.campaigns["bldc-flyer"] == "spring-2027"

    def test_a_taken_name_conflicts(self, world):
        create_code(dynamic(), OWNER, world.deps)
        different = CreateCodeRequest("bldc-flyer", CodeKind.DYNAMIC, NEW_DESTINATION)

        with pytest.raises(CodeTaken):
            create_code(different, OWNER, world.deps)

    def test_another_users_name_conflicts_even_with_the_same_fields(self, world):
        create_code(dynamic(), OWNER, world.deps)

        with pytest.raises(CodeTaken):
            create_code(dynamic(), STRANGER, world.deps)

    def test_repeating_a_request_succeeds_without_redoing_work(self, world):
        create_code(dynamic(), OWNER, world.deps)

        code, created = create_code(dynamic(), OWNER, world.deps)

        assert not created
        assert status_of(code) == CodeStatus.LIVE
        assert world.redirects.calls == 1
        assert len(world.renderer.calls) == 1

    def test_publish_failure_leaves_the_code_pending(self, world):
        world.redirects.fail = True

        with pytest.raises(StepFailed) as caught:
            create_code(dynamic(), OWNER, world.deps)

        assert caught.value.step == "publish"
        assert status_of(world.repository.get("bldc-flyer")) == CodeStatus.PENDING
        assert world.renderer.calls == []

    def test_render_failure_leaves_the_code_pending(self, world):
        world.renderer.fail = True

        with pytest.raises(StepFailed) as caught:
            create_code(dynamic(), OWNER, world.deps)

        assert caught.value.step == "render"
        assert status_of(world.repository.get("bldc-flyer")) == CodeStatus.PENDING

    def test_repeating_after_a_failure_finishes_the_code(self, world):
        world.renderer.fail = True
        with pytest.raises(StepFailed):
            create_code(dynamic(), OWNER, world.deps)
        world.renderer.fail = False

        code, created = create_code(dynamic(), OWNER, world.deps)

        assert not created
        assert status_of(code) == CodeStatus.LIVE


class TestUtm:
    """The UTM rule."""

    def test_campaign_adds_utm_parameters(self):
        url = build_final_url("bldc-flyer", DESTINATION, "bldc")

        assert url == (
            f"{DESTINATION}?utm_source=qr&utm_medium=print"
            "&utm_campaign=bldc&utm_content=bldc-flyer"
        )

    def test_blank_campaign_leaves_the_url_unchanged(self):
        assert build_final_url("one-off", DESTINATION, "") == DESTINATION

    def test_existing_query_and_fragment_are_kept(self):
        url = build_final_url("c", "https://example.com/a?x=1#top", "bldc")

        assert url.startswith("https://example.com/a?x=1&utm_source=qr")
        assert url.endswith("#top")

    def test_campaign_is_encoded(self):
        assert "utm_campaign=spring+2027" in build_final_url("c", DESTINATION, "spring 2027")


class TestChange:
    """BC2."""

    def test_new_destination_is_republished_without_rendering(self, world):
        create_code(dynamic(campaign=""), OWNER, world.deps)

        code = change_code(
            ChangeCodeRequest("bldc-flyer", destination=NEW_DESTINATION), OWNER, world.deps
        )

        assert world.redirects.published["bldc-flyer"] == NEW_DESTINATION
        assert code.version == 2
        assert len(world.renderer.calls) == 1

    def test_label_change_does_not_republish(self, world):
        create_code(dynamic(), OWNER, world.deps)

        change_code(ChangeCodeRequest("bldc-flyer", label="New label"), OWNER, world.deps)

        assert world.redirects.calls == 1

    def test_blank_campaign_removes_utm_parameters(self, world):
        create_code(dynamic(), OWNER, world.deps)

        code = change_code(ChangeCodeRequest("bldc-flyer", campaign=""), OWNER, world.deps)

        assert code.final_url == DESTINATION

    def test_a_new_campaign_is_republished(self, world):
        create_code(dynamic(), OWNER, world.deps)

        change_code(ChangeCodeRequest("bldc-flyer", campaign="summer"), OWNER, world.deps)

        assert world.redirects.campaigns["bldc-flyer"] == "summer"

    def test_a_badly_formed_campaign_is_refused(self, world):
        create_code(dynamic(), OWNER, world.deps)

        with pytest.raises(InvalidInput) as caught:
            change_code(ChangeCodeRequest("bldc-flyer", campaign="a:b"), OWNER, world.deps)

        assert caught.value.errors[0].field == "campaign"

    def test_static_destination_cannot_change(self, world):
        create_code(static(), OWNER, world.deps)

        with pytest.raises(InvalidInput):
            change_code(
                ChangeCodeRequest("menu-card", destination=NEW_DESTINATION), OWNER, world.deps
            )

    def test_static_campaign_cannot_change(self, world):
        create_code(static(), OWNER, world.deps)

        with pytest.raises(InvalidInput):
            change_code(ChangeCodeRequest("menu-card", campaign="new"), OWNER, world.deps)

    def test_static_label_can_change(self, world):
        create_code(static(), OWNER, world.deps)

        code = change_code(ChangeCodeRequest("menu-card", label="Menu"), OWNER, world.deps)

        assert code.label == "Menu"

    def test_a_new_label_that_is_too_long_is_refused(self, world):
        create_code(dynamic(), OWNER, world.deps)
        too_long = "a" * (MAX_LABEL_CHARACTERS + 1)

        with pytest.raises(InvalidInput) as caught:
            change_code(ChangeCodeRequest("bldc-flyer", label=too_long), OWNER, world.deps)

        assert caught.value.errors[0].field == "label"

    def test_new_destination_must_be_https(self, world):
        create_code(dynamic(), OWNER, world.deps)

        with pytest.raises(InvalidInput):
            change_code(
                ChangeCodeRequest("bldc-flyer", destination="http://x.com"), OWNER, world.deps
            )

    def test_another_users_code_looks_missing(self, world):
        create_code(dynamic(), OWNER, world.deps)

        with pytest.raises(CodeNotFound):
            change_code(ChangeCodeRequest("bldc-flyer", label="x"), STRANGER, world.deps)

    def test_empty_change_republishes(self, world):
        create_code(dynamic(), OWNER, world.deps)

        change_code(ChangeCodeRequest("bldc-flyer"), OWNER, world.deps)

        assert world.redirects.calls == 2

    def test_publish_failure_keeps_the_old_redirect_and_goes_pending(self, world):
        original, _ = create_code(dynamic(campaign=""), OWNER, world.deps)
        world.redirects.fail = True

        with pytest.raises(StepFailed):
            change_code(
                ChangeCodeRequest("bldc-flyer", destination=NEW_DESTINATION), OWNER, world.deps
            )

        assert world.redirects.published["bldc-flyer"] == original.final_url
        assert status_of(world.repository.get("bldc-flyer")) == CodeStatus.PENDING

    def test_empty_change_repairs_a_pending_code(self, world):
        create_code(dynamic(campaign=""), OWNER, world.deps)
        world.redirects.fail = True
        with pytest.raises(StepFailed):
            change_code(
                ChangeCodeRequest("bldc-flyer", destination=NEW_DESTINATION), OWNER, world.deps
            )
        world.redirects.fail = False

        code = change_code(ChangeCodeRequest("bldc-flyer"), OWNER, world.deps)

        assert status_of(code) == CodeStatus.LIVE
        assert world.redirects.published["bldc-flyer"] == NEW_DESTINATION

    def test_a_slower_writer_cannot_leave_a_stale_redirect(self, world):
        create_code(dynamic(campaign=""), OWNER, world.deps)

        def newer_write_then_stale_overwrite() -> None:
            stored = world.repository.get("bldc-flyer")
            world.repository.update(
                stored.model_copy(
                    update={
                        "destination": NEW_DESTINATION,
                        "final_url": NEW_DESTINATION,
                        "version": stored.version + 1,
                        "synced": True,
                    }
                )
            )
            world.redirects.published["bldc-flyer"] = "https://example.com/stale"

        world.repository.before_mark = newer_write_then_stale_overwrite

        change_code(ChangeCodeRequest("bldc-flyer", label="Slow"), OWNER, world.deps)

        assert world.redirects.published["bldc-flyer"] == NEW_DESTINATION

    def test_a_change_based_on_a_stale_read_is_refused(self, world):
        stale, _ = create_code(dynamic(), OWNER, world.deps)
        world.repository.codes["bldc-flyer"] = stale.model_copy(
            update={"label": "Newer", "version": stale.version + 1}
        )
        world.repository.get = lambda _name: stale

        with pytest.raises(CodeConflict):
            change_code(ChangeCodeRequest("bldc-flyer", label="Stale"), OWNER, world.deps)

        assert world.repository.codes["bldc-flyer"].label == "Newer"
        assert world.redirects.calls == 1


class TestView:
    """BC3."""

    def test_list_shows_only_the_callers_codes(self, world):
        create_code(dynamic("mine"), OWNER, world.deps)
        create_code(dynamic("theirs"), STRANGER, world.deps)

        views = list_codes(OWNER, world.deps)

        assert [view.code.code for view in views] == ["mine"]

    def test_another_users_code_looks_missing(self, world):
        create_code(dynamic(), OWNER, world.deps)

        with pytest.raises(CodeNotFound):
            get_code("bldc-flyer", STRANGER, world.deps)

    def test_dynamic_view_encodes_the_short_url(self, world):
        create_code(dynamic(), OWNER, world.deps)

        view = get_code("bldc-flyer", OWNER, world.deps)

        assert view.encoded_url == f"https://{REDIRECT_DOMAIN}/bldc-flyer"
        assert view.status == CodeStatus.LIVE
        assert view.allowed_actions == ["update", "download"]

    def test_static_view_encodes_the_final_url(self, world):
        create_code(static(), OWNER, world.deps)

        assert get_code("menu-card", OWNER, world.deps).encoded_url == DESTINATION

    def test_pending_code_cannot_be_downloaded(self, world):
        world.renderer.fail = True
        with pytest.raises(StepFailed):
            create_code(dynamic(), OWNER, world.deps)

        assert allowed_actions(world.repository.get("bldc-flyer")) == ["update"]


class TestImage:
    """BC4."""

    def test_default_image_is_served_without_rendering_again(self, world):
        create_code(dynamic(), OWNER, world.deps)

        url = get_image("bldc-flyer", "full", OWNER, world.deps)

        assert url.endswith("/bldc-flyer/full.png")
        assert len(world.renderer.calls) == 1

    def test_flower_renders_on_first_request_only(self, world):
        create_code(dynamic(), OWNER, world.deps)

        get_image("bldc-flyer", "flower", OWNER, world.deps)
        get_image("bldc-flyer", "flower", OWNER, world.deps)

        assert len(world.renderer.calls) == 2

    def test_pending_code_is_refused(self, world):
        world.renderer.fail = True
        with pytest.raises(StepFailed):
            create_code(dynamic(), OWNER, world.deps)

        with pytest.raises(CodeNotReady):
            get_image("bldc-flyer", "full", OWNER, world.deps)

    def test_unknown_logo_is_refused(self, world):
        create_code(dynamic(), OWNER, world.deps)

        with pytest.raises(InvalidInput):
            get_image("bldc-flyer", "giant", OWNER, world.deps)

    def test_another_users_code_looks_missing(self, world):
        create_code(dynamic(), OWNER, world.deps)

        with pytest.raises(CodeNotFound):
            get_image("bldc-flyer", "full", STRANGER, world.deps)

    def test_render_failure_is_reported(self, world):
        create_code(dynamic(), OWNER, world.deps)
        world.renderer.fail = True

        with pytest.raises(StepFailed):
            get_image("bldc-flyer", "flower", OWNER, world.deps)


class TestAlarmText:
    """The step-failures alarm matches log text, so every failure must use its terms."""

    @pytest.fixture
    def logged(self, monkeypatch) -> list[str]:
        """Capture the messages logged as errors."""
        messages: list[str] = []

        def record(message: str, *_args, **_kwargs) -> None:
            messages.append(message)

        monkeypatch.setattr(logger, "error", record)
        monkeypatch.setattr(logger, "exception", record)
        return messages

    @staticmethod
    def alarm_fires(messages: list[str]) -> bool:
        """Tell whether any message contains a term the alarm matches."""
        source = (REPO_ROOT / "infra/lib/api/http-api.ts").read_text()
        terms = re.search(r"anyTerm\('([^']+)', '([^']+)'\)", source)

        assert terms is not None
        return any(term in message for term in terms.groups() for message in messages)

    def test_a_failed_publish_alarms(self, world, logged):
        world.redirects.fail = True

        with pytest.raises(StepFailed):
            create_code(dynamic(), OWNER, world.deps)

        assert self.alarm_fires(logged)

    def test_a_failed_render_alarms(self, world, logged):
        world.renderer.fail = True

        with pytest.raises(StepFailed):
            create_code(dynamic(), OWNER, world.deps)

        assert self.alarm_fires(logged)

    def test_a_failed_image_render_alarms(self, world, logged):
        create_code(dynamic(), OWNER, world.deps)
        world.renderer.fail = True

        with pytest.raises(StepFailed):
            get_image("bldc-flyer", "flower", OWNER, world.deps)

        assert self.alarm_fires(logged)

    def test_a_code_that_keeps_changing_alarms(self, world, logged):
        world.repository.mark_complete = lambda *_args, **_kwargs: False

        with pytest.raises(StepFailed):
            create_code(dynamic(), OWNER, world.deps)

        assert self.alarm_fires(logged)


@pytest.mark.parametrize(
    ("path", "pattern"),
    [
        ("infra/lib/cloudfront-functions/redirect.js", r"const CODE_FORMAT = /(.+)/;"),
        ("infra/lib/analytics-stack.ts", r"const CODE_FORMAT = '(.+)';"),
    ],
)
def test_code_format_matches_everywhere_it_is_copied(path, pattern):
    """The format cannot be shared across languages, so the copies must not drift apart."""
    match = re.search(pattern, (REPO_ROOT / path).read_text())

    assert match is not None
    assert match.group(1) == CODE_PATTERN