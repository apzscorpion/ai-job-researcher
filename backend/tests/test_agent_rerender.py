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


def test_naukri_confirmation_counts_as_applied():
    from app.services.agent_apply import _CONFIRM
    assert _CONFIRM.search('Applied to "Sr. Angular Developer"\nSend Me Jobs Like This')
    assert not _CONFIRM.search("Apply to this job")


# hibob-style: hidden file input behind an "Add file" button; consent checkbox under a styled overlay
STYLED = """<input type=file id=file style=display:none><button type=button onclick="file.click()">Add file</button>
<label style="position:relative;display:inline-block"><input type=checkbox id=ok aria-label=consent>
<span style="position:absolute;inset:-4px;background:#fff"></span></label>"""


def test_upload_button_and_covered_checkbox(tmp_path):
    from playwright.async_api import async_playwright

    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4")

    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content(STYLED)
            obs, frames, _els = await _observe(page)
            assert '"id": "f0e1"' in obs and "Add file" in obs  # f0e0 is the hidden file input
            ids = {"Add file": "f0e1", "consent": "f0e2"}
            await _act(page, frames, {"type": "upload", "id": ids["Add file"], "file": "resume"}, {"resume": str(resume)})
            await _act(page, frames, {"type": "check", "id": ids["consent"]}, {})
            state = await page.evaluate("[file.files.length, ok.checked]")
            await browser.close()
            return state

    assert asyncio.run(run()) == [1, True]


# LinkedIn-style: the radio input is hidden, its label is what's shown
RADIOS = """<fieldset><legend>Do you have a valid driver's license?</legend>
<input type=radio name=dl id=y style="opacity:0;width:0;height:0;position:absolute"><label for=y>Yes</label>
<input type=radio name=dl id=n style="opacity:0;width:0;height:0;position:absolute"><label for=n>No</label></fieldset>"""


def test_hidden_radios_are_listed_with_their_question():
    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content(RADIOS)
            obs, frames, _els = await _observe(page)
            await _act(page, frames, {"type": "check", "id": "f0e0"}, {})
            checked = await page.evaluate("y.checked")
            await browser.close()
            return obs, checked

    obs, checked = asyncio.run(run())
    assert "Yes — Do you have a valid driver's license?" in obs and checked


def test_option_match_ignores_accents():
    from app.services.browser_apply import pick_option
    assert pick_option("Pathanamthitta", ["Pathankot, Punjab, India", "Pathanāmthitta, Kerala, India"]) \
        == "Pathanāmthitta, Kerala, India"


def test_naukri_chatbot_save_div_is_listed():
    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content("<div class=chatbot><div class=sendMsg>Save</div></div>")
            obs, _frames, _els = await _observe(page)
            await browser.close()
            return obs

    assert '"type": "button", "text": "Save"' in asyncio.run(run())
