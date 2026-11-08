# Brand image sources

- `appicon.png` (256x256 RGBA): https://static2.shortcutlabs.com/wp-content/uploads/2020/10/appicon.png (Shortcut Labs)
- `flicbutton.png` (512x512 palette, black line art): https://www.openhab.org/logos/flicbutton.png (the logo that the openHAB Flic binding uses)

Both images are Flic / Shortcut Labs artwork and trademarks. They are used here only to show which product the
integration is for. **Ask Flic for permission, or replace them, before you publish this repository.**

`original/` holds a neutral alternative that is our own artwork and not derived from the Flic logo: `icon.svg`,
`logo.svg` and rendered PNGs. Copy them to `custom_components/flic1/brand/` to use them instead.

To rebuild the PNGs, run `python tools/build_brand.py`. It needs Chrome or Edge and writes to
`custom_components/flic1/brand/`. The `@2x` files are smooth 2x upscales, because the sources have no higher
resolution. Home Assistant requirements: https://contributors.home-assistant.io/core/integrations/brands/ (Home Assistant 2026.3 and newer).
