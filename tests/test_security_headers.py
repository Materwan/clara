"""The headers every response carries (api/middleware.py). The microphone is Clara's own origin's, for the dictation button
(docs/DECISIONS.md, « The web site may use the microphone, on a press only »); the camera and the location stay off."""

from __future__ import annotations


async def test_the_microphone_is_for_clara_own_origin_alone(client):
    headers = (await client.get("/health")).headers
    assert headers["permissions-policy"] == "camera=(), microphone=(self), geolocation=()"
    assert headers["x-frame-options"] == "DENY" and headers["referrer-policy"] == "no-referrer"
