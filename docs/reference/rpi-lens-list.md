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
| 10 | Ultra-wide **CS-mount** lens | **Arducam** (LN051) | Arducam / Amazon | **3.2 mm** | **120° HFoV** on the HQ camera | **F2.0** (fixed iris) | **12 MP class, 1/1.7" format** (fully covers the 1/2.3" IMX477) | no | CS-mount (not M12), manual focus, Φ28×30 mm, 54 g. Mounted on `dam-imx477cs-120` (cs marks the CS mount, disambiguating from the M12 fisheye unit `dam-imx477-120`) |
| 11 | Fisheye **CS-mount** lens | generic (AICO ACCF021163MP class) | Amazon / AliExpress | **2.1 mm** | **160° DFoV** (~140° H) at 1/2.7" | **F1.6** | **3 MP, 1/2.7" format** | no | CS-mount wide fisheye. Mounted on `dam-imx477-160-z1` (JAYANG5). Caveats on the IMX477: the 1/2.7" image circle is smaller than the 1/2.3" sensor — expect corner shading/vignetting and a slightly narrower effective angle; 3 MP-rated glass is soft at 12.3 MP but fine at our 1280×720 capture |
| 12 | CCTV IR **CS-mount** lens | **Arducam** | Arducam / Amazon | **6 mm** | **65° HFoV** on the IMX477 | **F1.2** | CCTV class (MP rating n/p), 1/2" format — covers the 1/2.3" IMX477 | IR-suitable — the lens Arducam pairs with its IR-CUT day/night HQ camera (B0270); passes IR when the cut filter opens | CS-mount, manual focus + manual iris ring, Φ28×26 mm, 50.5 g. Normal-wide view on the HQ camera — between #8 (4 mm, ~75°) and #7/#9 (8 mm, ~42°) |
| 13 | Zoom lens **C-mount** (C-CS adapter incl.) | **Arducam** (LN057, model C2308ZM50) | Arducam / Amazon | **8–50 mm** varifocal | **45°–5.35° HFoV** at 1/2.3" | **F1.4** (12-blade manual iris) | box marks 3 MP; Arducam publishes no rating; 1/2.3" format | box marks IR; Arducam publishes no IR-correction claim — verify focus shift under night/IR light | C-mount + included C-CS adapter for the HQ camera, manual zoom/focus/iris rings, Φ40×68.3 mm, 148 g (heavy — needs mount support). Telephoto range for distant subjects; 3 MP-class glass is soft at 12.3 MP but fine at our 1280×720 capture |

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
- [Arducam LN051 3.2 mm 120° CS lens for the HQ camera](https://www.arducam.com/arducam-cs-lens-for-raspberry-pi-hq-camera-120-degree-ultra-wide-angle-cs-mount-lens-3-2mm-focal-length-with-manual-focus-ln051.html)
  and its [Amazon listing](https://www.amazon.com/Arducam-Raspberry-Camera-Degree-CS-Mount/dp/B08BR7WPR9)
- [AICO 2.1 mm F1.6 3 MP 160° CS fisheye (ACCF021163MP)](https://aico-lens.com/product/2-1mm-focal-length-wide-angle-fov-160-degree-cs-mount-cctv-lens-accf021163mp/)
  and a [generic 2.1 mm 3 MP CS listing (Amazon)](https://www.amazon.com/2-1mm-Mount-Camera-Security-Cameras/dp/B0BVFXRJRZ)
- [Arducam 6 mm CS wide-angle lens for the HQ camera](https://www.arducam.com/arducam-lens-for-raspberry-pi-high-quality-camera-wide-angle-cs-mount-lens-6mm-focal-length-with-manual-focus.html)
  and the [Arducam IR-CUT HQ camera bundle it ships with (B0270)](https://www.arducam.com/arducam-high-quality-ir-cut-camera-for-raspberry-pi-12-3mp-1-2-3-inch-imx477-hq-camera-module-with-6mm-cs-lens-for-pi-4b-3b-2b-3a-pi-zero-and-more.html)
- [Arducam LN057 8–50 mm C-mount zoom lens (C2308ZM50)](https://www.arducam.com/arducam-8-50mm-varifocal-c-mount-lens-for-raspberry-pi-hq-camera-with-c-cs-adapter-ln057.html)
  and its [Amazon listing](https://www.amazon.com/Arducam-8-50mm-C-Mount-Raspberry-Adapter/dp/B08PYMBX9T)
