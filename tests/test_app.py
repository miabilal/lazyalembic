from textual.widgets import Static

from lazyalembic.app import LazyAlembicApp


async def test_app_opens_with_chain_panel():
    app = LazyAlembicApp()
    async with app.run_test():
        assert app.query_one("#chain", Static) is not None


async def test_q_quits():
    app = LazyAlembicApp()
    async with app.run_test() as pilot:
        await pilot.press("q")
    assert app.return_code == 0
