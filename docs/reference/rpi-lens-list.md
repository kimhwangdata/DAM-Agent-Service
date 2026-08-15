# Raspberry Pi Camera Lens Inventory

Catalog of the M12/CCTV lenses we own for the fleet's cameras, from the
owner's descriptions enriched with what the sellers/manufacturers publish
(researched 2026-08-14; these are budget CCTV lens brands, so several
specs are simply not published — marked "n/p"). Companion docs:
`rpi-camera-list.md` (camera modules), `camera-info.md` (deployed
modules).

All of these are **M12 (S-mount)** board lenses unless noted — they fit
the HQ Camera **M12 variant** (`dam-imx477-8mm`), the Arducam M12
OV5647/IMX219 boards, and M12-mount IMX462 modules. They do NOT fit the
CS-mount HQ cameras without an adapter.

## Inventory table

| # | Lens | Maker / brand | Seller | Focal length | Stated FoV | Aperture | Rated / format | IR / night | Notes |
|---|------|---------------|--------|--------------|------------|----------|----------------|------------|-------|
| 1 | Wide CCTV IR lens | Shungcheng Electronic | AliExpress store | **2.8 mm** | **113°** | n/p (class-typical F2.0) | n/p | **IR lens** = IR-corrected — keeps focus under IR light; good pair for NoIR/night use | Standard wide surveillance lens |
| 2 | HD wide lens | Shungcheng Electronic | AliExpress store | n/p (~1.8–2.1 mm implied by angle) | **160°** | n/p | n/p | n/p | Near-fisheye wide; expect edge distortion |
| 3 | Fisheye | DPWestek Video Tech | AliExpress store | **1.44 mm** | **180°** | F2.0 (generic 1.44 mm class) | 5 MP, 1/2.5" | no | True fisheye; the 180° only holds on 1/2.5"–1/3" sensors |
| 4 | Starlight lens | unbranded ("Starlight") | — | **2.8 mm** | ~110–120° (class-typical, n/p) | low-light class (F1.2–1.8 typical, n/p) | **5 MP** | Starlight = wide aperture for low light | Pairs well with the IMX462 |
| 5 | Fisheye | **Zopsc** (model CW-BL14420-5MP) | Amazon | **1.56 mm** | **180°** | **F2.0** | **5 MP, 1/2.5"** | no | ABS body, 20×16.4 mm; 180° needs 1/2.5"–1/3" chip |
| 6 | Wide lens | **Novoxy** | Amazon | **2.8 mm** | ~110–120° (class-typical, n/p) | n/p (2.8 mm 5 MP class is F1.8–2.0) | **5 MP** | n/p | Same class as #1/#4 |
| 7 | Telephoto | unbranded | Shop1987458 Store (AliExpress) | **8 mm** | ~40–45° on 1/2.5" (n/p) | n/p | **2 MP (1080p)** | n/p | Narrow view — distant scenery; only 2 MP rated, soft on 12 MP sensors |
| 8 | Starlight lens | **Witrue** | AliExpress | **4 mm** | ~70–80° on 1/2.5" (n/p) | **F1.5** | **5 MP, 1/2.5"** | Starlight class, metal body, IR-suitable | The best documented of the set |
| 9 | Telephoto | **Witrue** | AliExpress | **8 mm** | ~40–45° on 1/2.5" (n/p) | n/p (8 mm class is ~F1.8) | 5 MP class (n/p) | n/p | Likely the lens on `dam-imx477-45-1` (hostname = 45° max FoV) |

## Practical notes for our fleet

- **Stated FoV depends on sensor format.** These angles assume the
  lens's design format (usually 1/2.5"). On the IMX477 (1/2.3", larger)
  the image circle may vignette on the ultra-wides; on smaller sensors
  (OV5647 1/4") the effective angle is narrower than stated.
- **Rated megapixels matter on the HQ camera**: a 2 MP-rated lens (#7)
  will look soft on the 12.3 MP IMX477 — fine for our 1280×720 capture
  size, visible if we ever raise it.
- **Focal length → use case** at 1/2.5": 1.44–1.56 mm = 180° fisheye
  (whole-sky/room), 2.8 mm = wide scenery (~110°), 4 mm = normal-wide
  (~75°), 8 mm = distant subject (~42°).
- **"Starlight"/wide-aperture lenses (#4, #8)** gather 2–4× more light
  than F2.8 glass — combine with the IMX462 and the agent's
  `NIGHT_EXPOSURE_MS` night mode for night locations. **IR-corrected
  lenses (#1)** hold focus under IR illumination — pair with the NoIR
  IMX219 + IR LEDs.
- **Fleet exposure policy (2026-08-14)**: IMX477 agents shoot city
  scenery with the **legacy stock AE** (night mode off — the ~66 ms
  city-lights look of `capture-24h.py` is intentional); the IMX462
  agent keeps **manual night mode** (`NIGHT_EXPOSURE_MS=1000`,
  `NIGHT_GAIN=4`) for truly dark sites (Camas-like, no city light).
- M12 lenses focus by screwing in/out of the holder; after any lens
  swap, refocus against the viewer at `http://<device>:8080/`.

## Sources

- [Witrue starlight 4 mm M12 (F1.5) review/listing](https://www.aliexpress.com/s/wiki-ssr/article/m12-cctv-lens-4mm)
  and a [Witrue 2.8 mm F2.8 160° listing](https://irl.grandado.com/products/witrue-hd-camera-lens-cctv-5mp-2-8mm-m12-mount-aperture-f2-8-1-2-quot-160-degree-for-surveillance-security-camera)
- [Zopsc 1.56 mm F2.0 5 MP fisheye (Amazon)](https://www.amazon.com/Zopsc-1-56mm-Fisheye-Professional-Surveillance/dp/B07VYJM8T5)
- [Generic 1.44 mm 180° 5 MP fisheye specs (Amazon)](https://www.amazon.com/Surveillance-Camera-Lenses-1-44Mm-Fisheye/dp/B0CCSTCYBL)
  and [Alibaba listing](https://www.alibaba.com/product-detail/Fisheye-lens-1-44mm-1-2_60732057433.html)
- [2.8 mm 5 MP M12 class specs (F1.8, ~120°)](https://aico-lens.com/product/normal-2-8-mm-focal-length-5mp-f1-8-m12-s-mount-cctv-board-lens-actm1228ir5mp3/)
- [M12 lens types and specifications guide](https://www.superiorcctv.com/different-types-of-m12-lenses-and-how-to-choose/)
