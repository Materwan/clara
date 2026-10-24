"""The Discord page: which channel carries which kind of message (chosen on the web site), what the bot says about
itself, and a test message per channel. The bot's side is in test_discord.py."""

from __future__ import annotations

import pytest

from clara.events import KINDS
from clara.events.routing import BY_ID, CATEGORIES, CHANNELS, CHOICES, category_of, check_routes
from conftest import parse_sse


@pytest.fixture
def bot(services) -> dict[str, str]:
    """The headers of the bot: its own token, client discord."""
    return {"Authorization": f"Bearer {services.tokens.create('bot', 'discord')[1]}", "X-Clara-Client": "discord"}


def beat(client, headers, **body):
    return client.post("/v1/clients/heartbeat", json={"state": "connected", **body}, headers=headers)


async def stream_as(client, services, headers) -> list[dict]:
    """What a client is sent: the server is told it is closing so the stream ends after what was missed."""
    services.events.closed = True
    response = await client.get("/v1/events/stream", params={"after": 0}, headers=headers)
    assert response.status_code == 200
    return [event for event in parse_sse(response.text) if event["type"] != "server"]


# ----------------------------------------------------------------------
# Categories
# ----------------------------------------------------------------------
@pytest.mark.parametrize(("kind", "source", "payload", "category"), [
    ("reminder", "reminders", {}, "reminders"),
    ("task", "tasks", {}, "tasks"),
    ("schedule", "schedules", {}, "schedules"),
    ("schedule", "triggers", {"status": "ok"}, "hook_results"),
    ("schedule", "triggers", {"status": "failed"}, "hook_failures"),
    ("schedule", "triggers", {"status": "interrupted"}, "hook_failures"),
    ("schedule", "triggers", {}, "hook_failures"),
    ("notification", "triggers", {}, "hook_notices"),
    ("notification", "mail", {}, "mail"),
    ("notification", "clara", {}, "clara_notices"),
    ("notification", "server", {}, "answers"),
    ("notification", "client:script", {}, "notifications"),
    ("notification", "anything else", {}, "notifications"),
    ("update", "server", {}, "updates"),
    ("alert", "server", {"key": "disk"}, "alerts"),
])
def test_each_message_has_a_category(kind, source, payload, category):
    assert category_of(kind, source, payload) == category


@pytest.mark.parametrize("kind", ["approval", "approval_resolved", "server", "ask"])
def test_approvals_questions_and_server_states_are_not_routed(kind):
    assert category_of(kind, "server", {}) is None


def test_every_message_the_bot_posts_has_a_category():
    from clara_discord.bot import ICONS

    assert set(ICONS) <= set(KINDS)
    assert all(category_of(kind, "x", {}) for kind in ICONS)


def test_the_defaults_are_what_the_bot_did_before():
    from clara_discord.bot import ROUTES

    for kind, channel in ROUTES.items():
        assert BY_ID[category_of(kind, "x", {})].default == channel
    assert [category.id for category in CATEGORIES if category.default != "notification"] == ["alerts"]
    assert [category.id for category in CATEGORIES if not category.can_be_off] == ["alerts"]


# ----------------------------------------------------------------------
# The setting
# ----------------------------------------------------------------------
def test_routes_keep_only_what_differs_from_the_default(services):
    saved = services.runtime.set("discord_routes", {"mail": "general", "tasks": "notification", "alerts": "alerts", "answers": "off"})
    assert saved == {"answers": "off", "mail": "general"}
    assert services.runtime.get("discord_routes") == saved
    services.runtime.reset("discord_routes")
    assert services.runtime.get("discord_routes") == {}


@pytest.mark.parametrize(("value", "why"), [
    ({"nothing": "general"}, "unknown category"),
    ({"mail": "approval"}, "a channel is"),  # #approval carries only what waits for your answer
    ({"mail": "elsewhere"}, "a channel is"),
    ({"mail": None}, "a channel is"),
    ({"alerts": "off"}, "cannot be switched off"),
    (["mail"], "an object"),
])
def test_a_wrong_route_is_refused_and_nothing_is_saved(services, value, why):
    with pytest.raises(ValueError, match=why):
        services.runtime.set("discord_routes", value)
    assert services.runtime.get("discord_routes") == {}


def test_alerts_can_go_elsewhere_but_not_nowhere():
    assert check_routes({"alerts": "general"}) == {"alerts": "general"}
    assert set(CHOICES) == {"notification", "general", "alerts"} and "approval" in CHANNELS


# ----------------------------------------------------------------------
# What the bot is told
# ----------------------------------------------------------------------
async def test_the_bot_is_told_the_channel_of_each_event_and_other_clients_are_not(client, services, token, bot):
    services.events.publish("reminder", "Reminder", "Call", source="reminders")
    services.events.publish("notification", "New email", "x: y", source="mail")
    services.events.publish("alert", "Disk", "full")
    services.events.publish("approval", "Needs you", "x")
    services.events.publish("approval_resolved", "Request settled", "x: allowed")
    told = await stream_as(client, services, bot)
    assert [(event["type"], event.get("channel")) for event in told] == [
        ("reminder", "notification"), ("notification", "notification"), ("alert", "alerts"), ("approval", None),
        ("approval_resolved", None)]
    assert all("channel" not in event for event in await stream_as(client, services, {"Authorization": f"Bearer {token}"}))


async def test_a_change_of_routes_applies_to_the_next_event(client, services, bot):
    services.runtime.set("discord_routes", {"mail": "general", "reminders": "off"})
    services.events.publish("reminder", "Reminder", "Call", source="reminders")
    services.events.publish("notification", "New email", "x: y", source="mail")
    services.events.publish("notification", "Other", "z", source="clara")
    assert [event["channel"] for event in await stream_as(client, services, bot)] == ["off", "general", "notification"]


async def test_hook_results_and_failures_can_go_to_different_channels(client, services, bot):
    services.runtime.set("discord_routes", {"hook_failures": "alerts"})
    services.events.publish("schedule", "Hook: invoices", "done", payload={"status": "ok"}, source="triggers")
    services.events.publish("schedule", "Hook: invoices", "broke", payload={"status": "failed"}, source="triggers")
    assert [event["channel"] for event in await stream_as(client, services, bot)] == ["notification", "alerts"]


# ----------------------------------------------------------------------
# What the bot tells
# ----------------------------------------------------------------------
async def test_the_page_before_the_bot_has_ever_been_seen(client, services, bot):
    page = (await client.get("/v1/admin/discord")).json()
    assert page["bot"]["state"] == "not seen" and page["bot"]["seen_seconds"] is None and page["test"] is None
    assert [channel["id"] for channel in page["channels"]] == list(CHANNELS)
    assert not any(channel["known"] for channel in page["channels"])
    assert [(item["id"], item["channel"]) for item in page["categories"]][-1] == ("alerts", "alerts")
    assert {item["channel"] for item in page["categories"][:-1]} == {"notification"}
    assert page["choices"] == list(CHOICES)


async def test_a_heartbeat_tells_the_name_the_channels_and_the_rights_it_lacks(client, services, bot):
    reply = await beat(client, bot, bot="Clara#1234", channels={
        "general": {"name": "general", "missing": ["manage_threads"]}, "alerts": {"name": "my-alerts", "missing": []},
        "elsewhere": {"name": "ignored", "missing": []}})
    assert reply.status_code == 200
    page = (await client.get("/v1/admin/discord")).json()
    assert page["bot"]["state"] == "connected" and page["bot"]["name"] == "Clara#1234"
    by_id = {channel["id"]: channel for channel in page["channels"]}
    assert by_id["general"]["missing"] == ["manage_threads"] and by_id["general"]["name"] == "general"
    assert by_id["alerts"]["known"] and by_id["alerts"]["name"] == "my-alerts"
    assert not by_id["approval"]["known"] and not by_id["notification"]["known"]
    assert set(services.discord_report.channels) == {"general", "alerts"}  # what a client names beyond the four is not kept


async def test_what_a_client_says_is_limited(client, services, bot):
    too_many = {f"c{n}": {"name": "x", "missing": []} for n in range(9)}
    assert (await beat(client, bot, channels=too_many)).status_code == 422
    assert (await beat(client, bot, channels={"general": {"name": "x" * 101, "missing": []}})).status_code == 422
    assert (await beat(client, bot, channels={"general": {"name": "x", "missing": ["a"] * 13}})).status_code == 422
    assert (await beat(client, bot, bot="b" * 101)).status_code == 422
    assert (await beat(client, bot, test={"event": 1, "ok": False, "error": "e" * 301})).status_code == 422


async def test_only_the_discord_client_reports_about_discord(client, services, bot):
    other = {"Authorization": f"Bearer {services.tokens.create('script', 'script')[1]}", "X-Clara-Client": "script"}
    await client.post("/v1/clients/heartbeat", json={"state": "connected", "bot": "Impostor"}, headers=other)
    assert (await client.get("/v1/admin/discord")).json()["bot"]["name"] == ""


async def test_goodbye_forgets_what_was_reported(client, services, bot):
    await beat(client, bot, bot="Clara#1234", channels={"general": {"name": "general", "missing": []}},
               test={"event": 3, "ok": True})
    await client.post("/v1/clients/heartbeat", json={"state": "disconnected"}, headers=bot)
    page = (await client.get("/v1/admin/discord")).json()
    assert page["bot"]["state"] == "not seen" and page["bot"]["name"] == "" and page["test"] is None
    assert not any(channel["known"] for channel in page["channels"])


async def test_the_page_says_what_each_channel_receives_now(client, services):
    await client.put("/v1/admin/settings/discord_routes", json={"value": {"mail": "general", "reminders": "off"}})
    page = (await client.get("/v1/admin/discord")).json()
    by_id = {channel["id"]: channel["carries"] for channel in page["channels"]}
    assert "new mail" in by_id["general"] and "your conversation" in by_id["general"]
    assert "new mail" not in by_id["notification"] and "reminders" not in str(by_id)
    assert by_id["approval"] == ["approval requests, with buttons"] and by_id["alerts"] == ["alerts"]
    chosen = {item["id"]: item["channel"] for item in page["categories"]}
    assert chosen["mail"] == "general" and chosen["reminders"] == "off" and chosen["tasks"] == "notification"


async def test_the_routes_are_changed_through_the_settings_and_refused_when_wrong(client, services):
    assert (await client.put("/v1/admin/settings/discord_routes", json={"value": {"alerts": "off"}})).status_code == 422
    assert (await client.put("/v1/admin/settings/discord_routes", json={"value": {"mail": "approval"}})).status_code == 422
    assert services.runtime.get("discord_routes") == {}


# ----------------------------------------------------------------------
# Test messages
# ----------------------------------------------------------------------
async def test_no_test_message_is_posted_to_a_bot_that_is_not_there(client, services):
    reply = await client.post("/v1/admin/discord/test", json={"channel": "alerts"})
    assert reply.status_code == 409
    assert services.events.recent(10, ["notification"]) == []


async def test_a_test_message_is_for_the_bot_only_and_names_its_channel(client, services, bot):
    await beat(client, bot)
    reply = await client.post("/v1/admin/discord/test", json={"channel": "approval"})
    assert reply.status_code == 201 and reply.json()["channel"] == "approval"
    event = services.events.get(reply.json()["event"])
    assert event.targets == ("discord",) and not event.for_client("web") and not event.for_client("cli")
    assert event.source == "discord-test" and event.payload == {"discord_test": "approval"}
    assert "#approval" in event.text and "approval requests" in event.text
    bot = await stream_as(client, services, bot)
    assert [(item["type"], item["channel"]) for item in bot] == [("notification", "approval")]


async def test_the_test_goes_where_it_says_whatever_the_routes_say(client, services, bot):
    services.runtime.set("discord_routes", {"mail": "general", "alerts": "general"})
    await beat(client, bot)
    reply = await client.post("/v1/admin/discord/test", json={"channel": "alerts"})
    assert [item["channel"] for item in await stream_as(client, services, bot)] == ["alerts"]
    assert services.events.get(reply.json()["event"]).text == "This is #alerts. It receives: nothing at the moment."  # alerts moved to #general


async def test_a_message_that_is_not_a_test_cannot_choose_its_channel(client, services, bot):
    """A client posting a notification cannot pass for a test: the source is the server's."""
    await client.post("/v1/notifications", json={"text": "hello", "targets": ["discord"]})
    event = services.events.recent(1, ["notification"])[0]
    assert event.source.startswith("client:")
    services.events.publish("notification", "Forged", "x", payload={"discord_test": "approval"}, source="client:evil")
    assert [item["channel"] for item in await stream_as(client, services, bot)] == ["notification", "notification"]


async def test_a_wrong_channel_is_refused(client, services, bot):
    await beat(client, bot)
    assert (await client.post("/v1/admin/discord/test", json={"channel": "elsewhere"})).status_code == 422
    assert (await client.post("/v1/admin/discord/test", json={})).status_code == 422


async def test_test_messages_are_limited(client, services, bot):
    await beat(client, bot)
    codes = [(await client.post("/v1/admin/discord/test", json={"channel": "general"})).status_code for _ in range(8)]
    assert codes == [201] * 6 + [429, 429]


async def test_how_the_last_test_went_comes_back_in_the_next_heartbeat(client, bot):
    await beat(client, bot)
    event = (await client.post("/v1/admin/discord/test", json={"channel": "general"})).json()["event"]
    assert (await client.get("/v1/admin/discord")).json()["test"] is None
    await beat(client, bot, test={"event": event, "ok": False, "error": "Missing Permissions"})
    seen = (await client.get("/v1/admin/discord")).json()["test"]
    assert (seen["event"], seen["ok"], seen["error"]) == (event, False, "Missing Permissions") and seen["at"]
    await beat(client, bot)  # the next ordinary heartbeat does not erase it
    assert (await client.get("/v1/admin/discord")).json()["test"]["event"] == event


async def test_a_silent_bot_is_called_so(client, services, monkeypatch, bot):
    await beat(client, bot)
    assert (await client.get("/v1/admin/discord")).json()["bot"]["state"] == "connected"
    services.clients["discord"] -= 301
    page = (await client.get("/v1/admin/discord")).json()
    assert page["bot"]["state"] == "silent"
    assert (await client.post("/v1/admin/discord/test", json={"channel": "general"})).status_code == 409
