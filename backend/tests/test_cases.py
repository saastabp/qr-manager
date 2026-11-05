"""Tests for the business cases, using in-memory fakes. See docs/DESIGN.md, section 3."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fakes import OWNER, REDIRECT_DOMAIN, STRANGER, FakeWorld

from cases.change_code import ChangeCodeRequest, change_code
from cases.create_code import CreateCodeRequest, create_code
from cases.errors import CodeNotFound, CodeNotReady, CodeTaken, InvalidInput, StepFailed
from cases.get_image import get_image
from cases.policies import allowed_actions, status_of
from cases.redirect_url import build_final_url
from cases.view_codes import get_code, list_codes
from models.qr_code import CODE_PATTERN, CodeKind, CodeStatus

DESTINATION = "https://example.com/page"
NEW_DESTINATION = "https://example.com/new"


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

    def test_final_url_must_fit_in_the_redirect_store(self, world):
        long_url = f"https://example.com/{'a' * 1100}"

        with pytest.raises(InvalidInput):
            create_code(CreateCodeRequest("ok", CodeKind.DYNAMIC, long_url), OWNER, world.deps)

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


def test_code_format_matches_the_redirect_function():
    """The format exists in Python and JavaScript, so the two must not drift apart."""
    source = Path(__file__).parents[2] / "infra/lib/cloudfront-functions/redirect.js"
    match = re.search(r"const CODE_FORMAT = /(.+)/;", source.read_text())

    assert match is not None
    assert match.group(1) == CODE_PATTERN