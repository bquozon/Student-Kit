// Swiss Student Kit: progressive enhancement only. Without JS every link and form still works on the server.
"use strict";
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const hit = (h) => { try { navigator.sendBeacon("/hit", JSON.stringify(h)); } catch (e) { /* stats are optional */ } };

  // ---------------------------------------------------------------- city page: search + university filter
  const q = $("#q"), seg = $("#seg"), count = $("#count");
  if (q && seg && count) {
    const params = new URLSearchParams(location.search);
    let u = params.get("u") || "";
    let timer;
    const apply = () => {
      const t = q.value.trim().toLowerCase();
      let shown = 0;
      for (const sec of $$("section.cat")) {
        let k = 0;
        for (const c of $$(".card", sec)) {
          const ok = (!u || c.dataset.u === "ALL" || c.dataset.u.split(" ").includes(u)) && (!t || c.dataset.s.includes(t));
          c.hidden = !ok;
          k += ok;
        }
        sec.hidden = !k;
        shown += k;
      }
      count.textContent = `${shown} of ${count.dataset.total}`;
      $("#empty").hidden = shown > 0;
      const p = new URLSearchParams();
      if (u) p.set("u", u);
      if (t) p.set("q", q.value.trim());
      history.replaceState(null, "", location.pathname + (p.size ? "?" + p : "") + location.hash);
    };
    q.addEventListener("input", () => {
      apply();
      clearTimeout(timer);
      const t = q.value.trim();
      if (t.length >= 3) timer = setTimeout(() => hit({ t: "search", q: t }), 1500);
    });
    q.form.addEventListener("submit", (e) => { e.preventDefault(); apply(); });
    seg.addEventListener("click", (e) => {
      const a = e.target.closest("a[data-u]");
      if (!a) return;
      e.preventDefault();
      u = a.dataset.u;
      for (const b of $$("a", seg)) b.removeAttribute("aria-current");
      a.setAttribute("aria-current", "true");
      apply();
      hit({ t: "filter", u });
    });
    document.addEventListener("click", (e) => {
      const a = e.target.closest("a.name[data-id]");
      if (a) hit({ t: "click", id: a.dataset.id });
    });
    // close an open "…" menu when clicking elsewhere
    document.addEventListener("click", (e) => {
      for (const d of $$("details.more[open]")) if (!d.contains(e.target)) d.open = false;
    });
  }

  // ---------------------------------------------------------------- "I use this" without a page reload
  document.addEventListener("submit", async (e) => {
    const f = e.target.closest("form.usef");
    if (!f) return;
    e.preventDefault();
    const b = $("button", f);
    b.disabled = true;
    try {
      const r = await fetch(f.action, { method: "POST", body: new FormData(f), headers: { Accept: "application/json" } });
      if (r.status === 401) return void (location.href = "/login?next=" + encodeURIComponent(location.pathname));
      if (!r.ok) throw new Error(r.status);
      const d = await r.json();
      b.setAttribute("aria-pressed", String(d.used));
      $("span", b).textContent = d.count.toLocaleString("de-CH").replace(/['’]/g, " ");
    } catch (err) {
      f.submit();
    } finally {
      b.disabled = false;
    }
  });

  // ---------------------------------------------------------------- suggest form
  const form = $("#suggest");
  if (form) {
    const desc = $("#description"), counter = $("#desc-count"), city = $("#city"), url = $("#url"), out = $("#urlcheck");
    const n = () => (counter.textContent = `${desc.value.length}/140`);
    desc.addEventListener("input", n);
    n();
    const unis = () => {
      for (const l of $$("#unis label[data-city]")) {
        const show = city.value === "all" || l.dataset.city === city.value;
        l.hidden = !show;
        if (!show) $("input", l).checked = false;
      }
    };
    city.addEventListener("change", unis);
    unis();
    // "All students" and specific universities exclude each other
    $("#unis").addEventListener("change", (e) => {
      const all = $('#unis input[value="ALL"]');
      if (e.target === all && all.checked) for (const i of $$("#unis input")) i.checked = i === all;
      else if (e.target !== all && e.target.checked) all.checked = false;
      else if (!$$("#unis input:checked").length) all.checked = true;
    });
    let last = "";
    url.addEventListener("change", async () => {
      const v = url.value.trim();
      if (!/^https:\/\/[^/]+\.[^/]+/.test(v) || v === last) return;
      last = v;
      out.innerHTML = '<p class="hint">Checking…</p>';
      try {
        const d = await (await fetch("/suggest/check?url=" + encodeURIComponent(v), { headers: { Accept: "application/json" } })).json();
        out.textContent = "";
        if (d.dupes.length) {
          const box = document.createElement("div");
          box.className = "dupes";
          box.append("Already in the kit on this domain: ");
          d.dupes.forEach((x, i) => {
            const a = document.createElement("a");
            a.href = x.url; a.target = "_blank"; a.rel = "noopener";
            a.textContent = x.name + (x.status === "approved" ? "" : ` (${x.status})`);
            box.append(i ? ", " : "", a);
          });
          box.append(". Still a different service? Go ahead.");
          out.append(box);
        }
        if (d.reachable === false) {
          const p = document.createElement("p");
          p.className = "warn";
          p.textContent = "We couldn't reach this URL. Double-check it (you can still send it).";
          out.append(p);
        }
      } catch (err) { out.textContent = ""; }
    });
  }
})();
