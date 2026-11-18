# ACTA t250u — walls de alertas solo en el privado

Fecha: 2026-11-18. Orden Sander t250u. Sin fusión. `data/alerts/state.json` de main no se toca.

## Hallazgo
`dashboard_alerts.py` escribía la clave `walls` en `data/alerts/brief.json` y `data/alerts/state.json`, y `daily_update` las añadía al árbol público.

## Decisión
La copia pública de esos dos ficheros se escribe sin la clave `walls`. La copia completa queda en `data/alerts/private/` (gitignored). `daily_update` quita `walls` antes de `git add` y restaura la copia completa solo para el push a g8-macro-data.

## Sin cambios
Tests existentes. `cme_options.yml`. El fichero `data/alerts/state.json` ya commiteado en main.

## Pruebas
Los tests de huella congelada de `scripts/dashboard_alerts.py` fallan porque el fichero cambió y las huellas no se actualizan sin autorización. El resto de la comprobación local de brief/estado y del workflow quedó en el PR.

## Siguiente
Autorizar la nueva huella de `dashboard_alerts.py` antes de reactivar `daily_update`.
