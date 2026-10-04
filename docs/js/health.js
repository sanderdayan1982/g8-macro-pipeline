/* Publication health is independent of model signals and HTTP reachability. */
(function () {
    'use strict';
    var lastReport = null;
    function applyHeader() {
        var r = lastReport, age = r ? (Date.now() - Date.parse(r.generated_utc)) / 3600000 : NaN;
        var warning = !isFinite(age) || age > 56 || age < -0.1 ? 'SIN VERIFICAR' :
            r.issues.length ? r.issues.length + ' INCIDENCIAS' : r.unknown_count ? 'VERIFICACIÓN PARCIAL' : '';
        if (!warning) return;
        var count = document.getElementById('status-count'), dots = document.getElementById('status-dots');
        if (count) count.textContent = warning;
        if (dots) { dots.textContent = '●'; dots.style.color = '#ff8c42'; dots.title = 'Ver vigilancia de publicaciones'; }
    }
    function render(report, now) {
        lastReport = report;
        applyHeader();
        var box = document.getElementById('publication-health');
        if (!box) return;
        box.replaceChildren();
        var age = (now - Date.parse(report.generated_utc)) / 3600000;
        var old = !isFinite(age) || age > 56 || age < -0.1;
        var issues = report.issues || [];
        var title = document.createElement('strong');
        title.textContent = old ? 'VIGILANCIA SIN ACTUALIZAR — no se puede confirmar la frescura' :
            issues.length ? 'FRESCURA: ' + issues.length + ' incidencias abiertas' :
            report.unknown_count ? 'FRESCURA: sin retrasos detectados · ' + report.unknown_count + ' calendarios sin confirmar' :
            'FRESCURA: publicaciones esperadas recibidas';
        title.style.color = old || issues.length ? '#ff8c42' : report.unknown_count ? '#fdb813' : '#2fbf71';
        box.appendChild(title);
        var stamp = document.createElement('p');
        stamp.textContent = 'Comprobación UTC: ' + (report.generated_utc || 'desconocida') +
            ' · fecha del dato ≠ fecha de descarga · EST nunca equivale a dato oficial.';
        box.appendChild(stamp);
        var details = document.createElement('details'), summary = document.createElement('summary');
        summary.textContent = 'Ver fuentes, observaciones y evaluación definitiva CTF';
        details.appendChild(summary);
        var table = document.createElement('table');
        table.className = 'attr-table';
        var rows = [['Fuente / salida', 'Estado', 'Última observación', 'Esperada']].concat(
            (report.feeds || []).map(function (x) { return [x.file, x.status, x.have_max || '—', x.expected_obs || 'sin calendario exacto']; }));
        rows.forEach(function (r, i) {
            var tr = document.createElement('tr');
            r.forEach(function (value) { var td = document.createElement(i ? 'td' : 'th'); td.textContent = value; tr.appendChild(td); });
            table.appendChild(tr);
        });
        details.appendChild(table); box.appendChild(details);
    }
    async function load() {
        var box = document.getElementById('publication-health');
        try {
            var r = await window.G8NET.fetchAny('https://raw.githubusercontent.com/sanderdayan1982/g8-macro-pipeline/main/data/_ingest/health.json');
            if (!r.ok) throw new Error('health unavailable');
            render(JSON.parse(r.text), Date.now());
        } catch (e) {
            lastReport = null; applyHeader();
            box.textContent = 'VIGILANCIA NO DISPONIBLE — no se puede confirmar la frescura. Reintento automático.';
            box.style.color = '#ff8c42';
        }
    }
    window.G8PublicationHealth = { render: render, refresh: load, applyHeader: applyHeader };
    document.addEventListener('DOMContentLoaded', function () { load(); setInterval(load, 15 * 60 * 1000); });
})();
