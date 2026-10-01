# ACTA P-2 — Dashboard: respaldo propio de raw.github y §00 resistente a caídas de red (1-oct-2026)

## Hallazgo
- Todos los módulos leen `data/` de raw.githubusercontent.com. Si el directo falla, G8NET solo tenía los
  proxies públicos (corsproxy.io da 403 en orígenes de producción; allorigins y codetabs van y vienen).
- §00 (brief.json) no pasaba por G8NET: un único `fetch` con 10 s de espera, sin cascada ni caché. Sin red,
  §00 se quedaba en error aunque el navegador hubiera descargado el brief minutos antes.

## Decisión
1. `docs/_redirects`: nueva ruta Netlify `/proxy/raw/*` → `https://raw.githubusercontent.com/:splat` (200).
2. G8NET (`docs/index.html`):
   - `BACKUP_PROXY_MAP`: para raw.githubusercontent.com el orden es directo → `/proxy/raw/` (via `netlify-raw`)
     → proxies públicos. Abriendo el fichero en local (`file:`) no se usa la ruta propia. Resto de hosts sin cambios.
   - Opción `staleOk` (opt-in) en `fetchAny`/`fetchChain`: si fallan todos los intentos en vivo, devuelve la
     última copia en caché de cualquier antigüedad, marcada `stale: true` con `cachedTs` y el error.
     En `fetchChain`, una copia vieja nunca gana a una URL posterior que responda en vivo.
     Los demás módulos no la usan: siguen igual.
3. §00: `G8NET.fetchChain([brief.json], { corsOk, noCache, staleOk })` en lugar del fetch único de 10 s.
   `noCache` mantiene el comportamiento anterior (siempre en vivo primero); cada lectura en vivo actualiza la caché.
   Sin red → pinta la copia en caché con aviso rojo «SIN RED … descargada el AAAA-MM-DD HH:MM (hora local)» y chip
   «§00 SIN RED · caché» en la cabecera. El aviso de BRIEF ATRASADO (edad de `generated_utc`) sigue funcionando
   sobre esa copia. Sin red y sin copia → error explícito.

## Sin cambios
Fórmulas, umbrales, z, ACM, s01b, factor USD, contenido de brief.json. Caché de 15 min del resto de módulos.
Nota: el botón «↻ datos» borra también la copia de §00 (borra todas las claves `g8net_`).

## Pruebas
`tests/test_p2_dashboard.py` (10): ejecuta la capa G8NET real en Node con fetch/localStorage simulados —
orden directo → netlify-raw → 3 públicos; netlify-raw gana si cae el directo; sin ruta propia en `file:`;
otros hosts sin cambios; sin red + caché → copia con hora solo con `staleOk`; sin red sin caché → falla;
`noCache` va en vivo y refresca la caché; una copia vieja no gana a un tier en vivo. Estáticos: ruta en
`_redirects`; §00 usa fetchChain y ya no el fetch único.
Comprobado además en el navegador (servidor local): con red §00 se pinta y guarda caché; con fetch caído
recorre los 5 intentos en orden y muestra la copia con su hora.
Suite completa (Python 3.11 + requirements + pyyaml): 617 tests OK (5 omitidos: 1 de red en vivo y 4 casos
que no aplican; antes de P-1 se contaban 10 porque faltaba pyyaml). La ruta `/proxy/raw/` solo se puede probar de verdad una vez desplegado en Netlify.

## Siguiente
Punto 2: AUD congelado desde el 23-sep (AUD_NOM_2Y, RY_G8_AUD, ACM_G8_AUD).
