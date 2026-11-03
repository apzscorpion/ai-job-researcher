import asyncio

from app.services import brain


def test_prompt_round_trip_and_timeout():
    async def run():
        assert not brain.connected(7)
        asking = asyncio.create_task(brain.ask(7, "fill the form", timeout=5))
        got = await brain.next_prompt(7, wait=2)
        assert got and got[1] == "fill the form" and brain.connected(7)
        assert await brain.next_prompt(7, wait=0) is None  # handed out once only
        assert brain.answer(7, got[0], '{"status": "continue"}')
        assert await asking == '{"status": "continue"}'
        assert not brain.answer(7, got[0], "again")  # already answered
        assert await brain.ask(7, "nobody answers", timeout=0.1) == ""  # caller falls back to its own AI
        assert await brain.next_prompt(8, wait=0) is None  # other users never see it

    asyncio.run(run())
