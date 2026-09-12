"""Color schemes are explicit, persistent and independent of light/dark mode."""
from pathlib import Path
import re
import pytest

from tests.test_frontend_requests import run_js


ROOT = Path(__file__).resolve().parents[1]


def test_four_named_color_schemes_are_available_in_header_and_settings():
    page = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    assert 'id="color-theme-select"' in page
    assert 'aria-label="界面配色"' in page
    for theme in ("mint", "ocean", "peach", "iris"):
        assert f'value="{theme}"' in page
        assert f'data-color-theme-option="{theme}"' in page


def test_color_scheme_selection_persists_without_changing_dark_mode():
    run_js('''
assert.strictEqual(vm.runInContext("typeof setColorTheme", sandbox), "function");
const values = new Map();
sandbox.localStorage = {setItem: (key, value) => values.set(key, value)};
sandbox.document.body.dataset = {theme: "dark"};
const setColorTheme = vm.runInContext("setColorTheme", sandbox);
setColorTheme("peach");
assert.strictEqual(sandbox.document.body.dataset.colorTheme, "peach");
assert.strictEqual(sandbox.document.body.dataset.theme, "dark");
assert.strictEqual(values.get("reagent-color-theme-v1"), "peach");
setColorTheme("unknown");
assert.strictEqual(sandbox.document.body.dataset.colorTheme, "mint");
sandbox.localStorage.setItem = () => { throw new Error("storage blocked"); };
setColorTheme("iris");
assert.strictEqual(sandbox.document.body.dataset.colorTheme, "iris");
''')


def test_color_scheme_restores_saved_value_and_survives_blocked_storage():
    run_js('''
assert.strictEqual(vm.runInContext("typeof bindColorThemes", sandbox), "function");
sandbox.document.body.dataset = {theme: "light"};
sandbox.localStorage = {getItem: () => "ocean"};
const bindColorThemes = vm.runInContext("bindColorThemes", sandbox);
bindColorThemes();
assert.strictEqual(sandbox.document.body.dataset.colorTheme, "ocean");
sandbox.localStorage.getItem = () => { throw new Error("storage blocked"); };
bindColorThemes();
assert.strictEqual(sandbox.document.body.dataset.colorTheme, "mint");
''')


def test_light_themes_use_tinted_surfaces_instead_of_pure_white():
    style = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
    for selector in ('body:not([data-theme="dark"])', *(f'body[data-color-theme="{theme}"]:not([data-theme="dark"])' for theme in ("ocean", "peach", "iris"))):
        block = style.split(selector + " {", 1)[1].split("}", 1)[0]
        surface = re.search(r'--bg-surface:\s*(#[0-9a-f]{6})', block)
        assert surface, f"{selector} must provide a coordinated surface tint"
        assert surface.group(1) != "#ffffff"
    aliases = style.split("body[data-color-theme] {", 1)[1].split("}", 1)[0]
    assert "--surface: var(--bg-surface)" in aliases
    assert "--sidebar-surface: var(--bg-surface)" in aliases


def _light_palette(theme):
    style = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
    tokens = {}
    selectors = ['body:not([data-theme="dark"])']
    if theme != "mint":
        selectors.append(f'body[data-color-theme="{theme}"]:not([data-theme="dark"])')
    for selector in selectors:
        block = style.split(selector + " {", 1)[1].split("}", 1)[0]
        tokens.update(re.findall(r'(--[\w-]+):\s*(#[0-9a-f]{6})', block))
    return tokens


def _luminance(color):
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
    return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))


def _contrast(first, second):
    values = sorted((_luminance(first), _luminance(second)))
    return (values[1] + .05) / (values[0] + .05)


@pytest.mark.parametrize("theme", ["mint", "ocean", "peach", "iris"])
def test_light_palette_canvas_is_visibly_colored_and_separates_layers(theme):
    colors = _light_palette(theme)
    canvas = _luminance(colors["--bg-canvas"])
    assert canvas <= .8, "large canvas must not remain almost white"
    assert _luminance(colors["--bg-surface"]) - canvas >= .06, "cards should lift off the colored canvas"
    assert canvas - _luminance(colors["--bg-subtle"]) >= .06, "sidebar needs its own darker layer"


@pytest.mark.parametrize("theme", ["mint", "ocean", "peach", "iris"])
def test_light_palette_has_dark_navigation_with_readable_complementary_selection(theme):
    colors = _light_palette(theme)
    for name in ("--rail-bg", "--rail-ink", "--rail-active-bg", "--rail-active-ink", "--panel-bg", "--companion-soft"):
        assert name in colors, f"missing semantic visual role: {name}"
    assert _luminance(colors["--rail-bg"]) < .12
    assert _contrast(colors["--rail-ink"], colors["--rail-bg"]) >= 7
    assert _contrast(colors["--rail-active-ink"], colors["--rail-active-bg"]) >= 4.5
    assert colors["--panel-bg"] != colors["--bg-surface"]
    assert colors["--companion-soft"] != colors["--accent-soft"]


@pytest.mark.parametrize("theme", ["mint", "ocean", "peach", "iris"])
def test_secondary_text_remains_readable_on_stronger_colored_layers(theme):
    colors = _light_palette(theme)
    for surface in ("--bg-subtle", "--bg-canvas", "--bg-surface"):
        assert _contrast(colors["--text-secondary"], colors[surface]) >= 4.5
