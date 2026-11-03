import asyncio

from app.services.agent_apply import _act, _observe

# Typing the name rebuilds the whole form (Vue/React validation does this), dropping the agent's element marks
HTML = """<form id=f></form><script>
const draw = () => f.innerHTML = '<input id=n aria-label=Name><textarea aria-label=Cover></textarea>' +
  '<input type=file aria-label=Resume><input type=checkbox aria-label=terms>';
draw(); f.addEventListener('input', e => { if (e.target.id === 'n') { const v = e.target.value; draw();
  document.getElementById('n').value = v; } });
</script>"""


def test_actions_survive_a_form_that_rerenders(tmp_path):
    from playwright.async_api import async_playwright

    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4")

    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content(HTML)
            _obs, frames, _els = await _observe(page)
            for action in [{"type": "fill", "id": "f0e0", "value": "Asif"}, {"type": "fill", "id": "f0e1", "value": "Hi"},
                           {"type": "upload", "id": "f0e2", "file": "resume"}, {"type": "check", "id": "f0e3"}]:
                await _act(page, frames, action, {"resume": str(resume)})
            state = await page.evaluate(
                "[n.value, document.querySelector('textarea').value, document.querySelector('[type=file]').files.length,"
                " document.querySelector('[type=checkbox]').checked]")
            await browser.close()
            return state

    assert asyncio.run(run()) == ["Asif", "Hi", 1, True]
