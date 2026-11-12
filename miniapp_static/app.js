(() => {
  const tg = window.Telegram?.WebApp;
  const initData = tg?.initData || window.__MINIAPP_DEMO_INIT_DATA__ || "";
  const pagesDemo = window.__MINIAPP_PAGES_DEMO__ === true || location.hostname.endsWith(".github.io");
  const demoStorageKey = "feed-miniapp-pages-demo-v1";
  const $ = (id) => document.getElementById(id);
  const fmt = (value, digits = 2) => Number(value || 0).toLocaleString("ar-EG", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
  let state;
  let noticeTimer;

  tg?.ready();
  tg?.expand();
  if (pagesDemo) $("export").textContent = "تنزيل CSV";
  $("send-to-bot").hidden = typeof tg?.sendData !== "function";

  function showNotice(message, error = false) {
    const notice = $("notice");
    notice.textContent = message;
    notice.classList.toggle("error", error);
    notice.hidden = false;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { notice.hidden = true; }, 5000);
  }

  async function api(path, options = {}) {
    if (pagesDemo) return pagesDemoApi(path, options);
    const relativePath = path.replace(/^\/+/, "");
    const response = await fetch(new URL(relativePath, document.baseURI), {
      ...options,
      headers: {
        Authorization: `tma ${initData}`,
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
    });
    if (!response.ok) {
      let message = "حصل خطأ، حاول مرة تانية.";
      try { message = (await response.json()).error || message; } catch {}
      throw new Error(message);
    }
    return response;
  }

  function makeDemoState(feeds, saved = {}) {
      const qty = Object.fromEntries(feeds.map((feed) => [
        String(feed.row),
        Number(saved.qty?.[feed.row] ?? feed.default_qty),
      ]));
      const prices = Object.fromEntries(feeds.map((feed) => [
        String(feed.row),
        Number(saved.prices?.[feed.row] || 0),
      ]));
      const target = saved.target == null ? null : Number(saved.target);
      const recipes = Array.isArray(saved.recipes) ? saved.recipes : [];
      const lines = feeds.filter((feed) => qty[String(feed.row)] > 0);
      const totalWeight = lines.reduce((sum, feed) => sum + qty[String(feed.row)], 0);
      const proteinKg = lines.reduce((sum, feed) => sum + qty[String(feed.row)] * feed.protein_pct / 100, 0);
      const costLines = lines.filter((feed) => prices[String(feed.row)] > 0);
      const costPerTon = costLines.length
        ? costLines.reduce((sum, feed) => sum + prices[String(feed.row)] * qty[String(feed.row)] / totalWeight, 0)
        : null;
      let advice = "";
      if (target && totalWeight > 0) {
        const current = proteinKg / totalWeight * 100;
        const diff = current - target;
        if (Math.abs(diff) < 0.25) {
          advice = `🎯 الهدف: ${fmt(target)}%\n✅ الحالي: ${fmt(current)}% وعلى الهدف`;
        } else {
          const source = diff < 0
            ? feeds.find((feed) => feed.row === 7) || [...feeds].sort((a, b) => b.protein_pct - a.protein_pct)[0]
            : feeds.find((feed) => feed.row === 5) || [...feeds].filter((feed) => feed.protein_pct > 0).sort((a, b) => a.protein_pct - b.protein_pct)[0];
          if (source && (diff < 0 ? source.protein_pct > target : source.protein_pct < target)) {
            const x = diff < 0
              ? (target / 100 * totalWeight - proteinKg) / (source.protein_pct / 100 - target / 100)
              : (proteinKg - target / 100 * totalWeight) / (target / 100 - source.protein_pct / 100);
            advice = `🎯 الهدف: ${fmt(target)}%\n💡 ضيف حوالي ${fmt(x, 1)} كجم ${source.name} للوصول للهدف`;
          }
        }
      }
      return {
        feeds,
        qty,
        prices,
        target,
        recipes,
        result: {
          total_weight: totalWeight,
          protein_kg: proteinKg,
          cp_pct: totalWeight > 0 ? proteinKg / totalWeight * 100 : 0,
          cost_per_ton: costPerTon,
          missing_price: lines.filter((feed) => !prices[String(feed.row)]).map((feed) => feed.name),
          lines: lines.map((feed) => ({
            row: feed.row,
            name: feed.name,
            qty: qty[String(feed.row)],
            share: qty[String(feed.row)] / totalWeight * 100,
            protein: qty[String(feed.row)] * feed.protein_pct / 100,
            price: prices[String(feed.row)],
          })),
          advice,
        },
      };
    }

  function saveDemoState(nextState) {
      localStorage.setItem(demoStorageKey, JSON.stringify({
        qty: nextState.qty,
        prices: nextState.prices,
        target: nextState.target,
        recipes: nextState.recipes,
      }));
      return nextState;
    }

  async function pagesDemoApi(path, options = {}) {
      const method = options.method || "GET";
      const payload = options.body ? JSON.parse(options.body) : {};
      let status = 200;
      let body;
      if (path === "/api/state" && method === "GET") {
        body = state;
      } else if (path === "/api/state" && method === "PUT") {
        let qty = { ...state.qty };
        if (payload.reset) qty = Object.fromEntries(state.feeds.map((feed) => [String(feed.row), feed.default_qty]));
        Object.assign(qty, payload.qty || {});
        if (payload.scale_to) {
          const total = Object.values(qty).reduce((sum, value) => sum + Number(value), 0);
          if (total <= 0) {
            status = 400;
            body = { error: "أدخل وزنًا حاليًا أكبر من صفر." };
          } else {
            const factor = Number(payload.scale_to) / total;
            qty = Object.fromEntries(Object.entries(qty).map(([row, value]) => [row, Math.round(value * factor * 100) / 100]));
          }
        }
        if (status === 200) {
          state = makeDemoState(state.feeds, {
            qty,
            prices: { ...state.prices, ...(payload.prices || {}) },
            target: Object.hasOwn(payload, "target") ? payload.target : state.target,
            recipes: state.recipes,
          });
          body = saveDemoState(state);
        }
      } else if (path === "/api/calculate" && method === "POST") {
        body = state.result.total_weight > 0 ? state : { error: "أضف خامة واحدة على الأقل قبل الحساب." };
        if (state.result.total_weight <= 0) status = 400;
      } else if (path === "/api/recipes" && method === "POST") {
        const recipes = [...state.recipes];
        if (!payload.name || payload.name.length > 40) {
          status = 400;
          body = { error: "اكتب اسماً من 1 إلى 40 حرفاً." };
        } else if (payload.action === "save") {
          if (!recipes.includes(payload.name) && recipes.length >= 20) {
            status = 400;
            body = { error: "الحد الأقصى 20 خلطة محفوظة." };
          } else if (!state.result.lines.length) {
            status = 400;
            body = { error: "أضف خامة واحدة على الأقل قبل حفظ الخلطة." };
          } else {
            recipes.push(...(recipes.includes(payload.name) ? [] : [payload.name]));
            state = makeDemoState(state.feeds, {
              qty: state.qty,
              prices: state.prices,
              target: state.target,
              recipes,
            });
          }
        } else if (payload.action === "load" && recipes.includes(payload.name)) {
          const saved = JSON.parse(localStorage.getItem(`${demoStorageKey}:recipe:${payload.name}`) || "{}");
          state = makeDemoState(state.feeds, { ...saved, recipes });
        } else if (payload.action === "delete" && recipes.includes(payload.name)) {
          localStorage.removeItem(`${demoStorageKey}:recipe:${payload.name}`);
          state = makeDemoState(state.feeds, {
            qty: state.qty,
            prices: state.prices,
            target: state.target,
            recipes: recipes.filter((name) => name !== payload.name),
          });
        } else {
          status = 404;
          body = { error: "الخلطة غير موجودة." };
        }
        if (status === 200) {
          if (payload.action === "save") {
            localStorage.setItem(`${demoStorageKey}:recipe:${payload.name}`, JSON.stringify({
              qty: state.qty,
              prices: state.prices,
              target: state.target,
            }));
          }
          body = saveDemoState(state);
        }
      } else if (path === "/api/export" && method === "POST") {
        if (!state.result.lines.length) {
          status = 400;
          body = { error: "أضف خامة واحدة على الأقل قبل تنزيل الملف." };
        } else {
          const rows = [
            ["الخامة", "الكمية كجم", "النسبة %", "البروتين كجم", "سعر الطن جنيه"],
            ...state.result.lines.map((line) => [line.name, line.qty, line.share, line.protein, line.price]),
            ["الوزن الكلي", state.result.total_weight, "", state.result.protein_kg, ""],
            ["نسبة البروتين %", state.result.cp_pct, "", "", ""],
            ["تكلفة الطن جنيه", state.result.cost_per_ton ?? "", "", "", ""],
          ];
          return new Response(new Blob(
            [`\uFEFF${rows.map((row) => row.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(",")).join("\r\n")}`],
            { type: "text/csv;charset=utf-8" },
          ));
        }
      } else {
        status = 404;
        body = { error: "الطلب غير موجود." };
      }
      if (status >= 400) {
        return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
  }

  function renderResult() {
    const result = state.result;
    $("protein").textContent = `${fmt(result.cp_pct)}٪`;
    $("weight").textContent = `${fmt(result.total_weight)} كجم`;
    $("cost").textContent = result.cost_per_ton == null ? "—" : `${fmt(result.cost_per_ton)} ج`;
    $("advice").textContent = result.advice || "";
    $("advice").hidden = !result.advice;
    const missing = result.missing_price;
    $("missing-prices").textContent = missing.length ? `أسعار ناقصة: ${missing.join("، ")}` : "";
    $("missing-prices").hidden = missing.length === 0;
    const lines = $("result-lines");
    lines.replaceChildren();
    for (const item of result.lines) {
      const line = document.createElement("div");
      line.className = "result-line";
      const name = document.createElement("span");
      name.textContent = item.name;
      const details = document.createElement("span");
      details.textContent = `${fmt(item.qty)} كجم · ${fmt(item.share)}٪`;
      line.append(name, details);
      lines.append(line);
    }
    if (!result.lines.length) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "أضف خامة واحدة على الأقل عشان تظهر النتيجة.";
      lines.append(empty);
    }
  }

  function renderFeeds() {
    const list = $("feeds");
    const query = $("search").value.trim().toLocaleLowerCase("ar");
    const visible = state.feeds.filter((feed) => feed.name.toLocaleLowerCase("ar").includes(query));
    $("feed-count").textContent = `${state.result.lines.length} خامة مستخدمة`;
    list.replaceChildren();
    for (const feed of visible) {
      const card = document.createElement("article");
      card.className = "feed-card";
      const top = document.createElement("div");
      top.className = "feed-top";
      const name = document.createElement("strong");
      name.textContent = feed.name;
      const protein = document.createElement("span");
      protein.className = "protein-badge";
      protein.textContent = `بروتين ${fmt(feed.protein_pct)}٪`;
      top.append(name, protein);
      const inputs = document.createElement("div");
      inputs.className = "feed-inputs";
      inputs.append(
        makeFeedInput("الكمية (كجم)", "qty", feed, state.qty[String(feed.row)] || 0, "0.1"),
        makeFeedInput("سعر الطن (جنيه)", "price", feed, state.prices[String(feed.row)] || 0, "1"),
      );
      card.append(top, inputs);
      list.append(card);
    }
    if (!visible.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "مفيش خامة بالاسم ده.";
      list.append(empty);
    }
  }

  function makeFeedInput(label, kind, feed, value, step) {
    const wrapper = document.createElement("label");
    wrapper.textContent = label;
    const input = document.createElement("input");
    input.type = "number";
    input.min = "0";
    input.step = step;
    input.inputMode = "decimal";
    input.dataset.kind = kind;
    input.dataset.row = String(feed.row);
    input.value = value ? String(value) : "";
    input.placeholder = "0";
    input.addEventListener("change", () => syncInputs());
    wrapper.append(input);
    return wrapper;
  }

  function renderRecipes() {
    const list = $("recipes");
    list.replaceChildren();
    $("recipe-count").textContent = `${state.recipes.length} / 20`;
    if (!state.recipes.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "احفظ خلطتك عشان ترجع لها في أي وقت.";
      list.append(empty);
      return;
    }
    for (const recipeName of state.recipes) {
      const row = document.createElement("div");
      row.className = "recipe-item";
      const name = document.createElement("strong");
      name.textContent = recipeName;
      const actions = document.createElement("div");
      actions.className = "recipe-actions";
      const load = document.createElement("button");
      load.textContent = "استخدم";
      load.addEventListener("click", () => recipeAction("load", recipeName));
      const remove = document.createElement("button");
      remove.className = "delete";
      remove.textContent = "حذف";
      remove.addEventListener("click", () => recipeAction("delete", recipeName));
      actions.append(load, remove);
      row.append(name, actions);
      list.append(row);
    }
  }

  function render() {
    renderResult();
    renderFeeds();
    renderRecipes();
    $("target").value = state.target ?? "";
  }

  async function syncInputs(extra = {}) {
    const qty = {};
    const prices = {};
    for (const input of document.querySelectorAll("[data-kind]")) {
      const row = input.dataset.row;
      const value = input.value === "" ? 0 : Number(input.value);
      (input.dataset.kind === "qty" ? qty : prices)[row] = value;
    }
    const targetInput = $("target").value;
    try {
      state = await (await api("/api/state", {
        method: "PUT",
        body: JSON.stringify({
          qty,
          prices,
          target: targetInput === "" ? null : Number(targetInput),
          ...extra,
        }),
      })).json();
      renderResult();
      renderRecipes();
      if (extra.scale_to) renderFeeds();
      showNotice("تم تحديث الخلطة.");
      return true;
    } catch (error) {
      showNotice(error.message, true);
      return false;
    }
  }

  async function recipeAction(action, name) {
    try {
      state = await (await api("/api/recipes", {
        method: "POST",
        body: JSON.stringify({ action, name }),
      })).json();
      render();
      showNotice(action === "delete" ? "اتحذفت الخلطة." : "تم تحميل الخلطة.");
    } catch (error) {
      showNotice(error.message, true);
    }
  }

  $("search").addEventListener("input", renderFeeds);
  $("target").addEventListener("change", () => syncInputs());
  $("calculate").addEventListener("click", async () => {
    if (!await syncInputs()) return;
    try {
      state = await (await api("/api/calculate", { method: "POST" })).json();
      renderResult();
      showNotice("تم حساب الخلطة.");
    } catch (error) {
      showNotice(error.message, true);
    }
  });
  $("send-to-bot").addEventListener("click", async () => {
    if (!await syncInputs()) return;
    if (state.result.total_weight <= 0) {
      showNotice("أضف خامة واحدة على الأقل قبل الإرسال.", true);
      return;
    }
    const data = JSON.stringify({
      type: "feed_calculation",
      qty: state.qty,
      prices: state.prices,
      target: state.target,
    });
    if (new TextEncoder().encode(data).length > 4096) {
      showNotice("بيانات الخلطة أكبر من الحد المسموح للإرسال.", true);
      return;
    }
    try {
      tg.sendData(data);
    } catch (error) {
      showNotice(error.message || "تعذّر إرسال الخلطة إلى البوت.", true);
    }
  });
  $("reset").addEventListener("click", async () => {
    try {
      state = await (await api("/api/state", {
        method: "PUT",
        body: JSON.stringify({ reset: true }),
      })).json();
      render();
      showNotice("بدأنا خلطة جديدة.");
    } catch (error) {
      showNotice(error.message, true);
    }
  });
  $("scale").addEventListener("click", () => {
    const scaleTo = Number($("scale-to").value);
    if (!scaleTo) return showNotice("اكتب الوزن الإجمالي المطلوب.", true);
    syncInputs({ scale_to: scaleTo });
  });
  $("save-recipe").addEventListener("click", async () => {
    const name = $("recipe-name").value.trim();
    if (!name) return showNotice("اكتب اسماً للخلطة.", true);
    if (!await syncInputs()) return;
    try {
      state = await (await api("/api/recipes", {
        method: "POST",
        body: JSON.stringify({ action: "save", name }),
      })).json();
      renderRecipes();
      $("recipe-name").value = "";
      showNotice("اتحفظت الخلطة.");
    } catch (error) {
      showNotice(error.message, true);
    }
  });
  $("export").addEventListener("click", async () => {
    const button = $("export");
    button.disabled = true;
    try {
      if (!await syncInputs()) return;
      const response = await api("/api/export", { method: "POST" });
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = pagesDemo ? "Feed_Calculation.csv" : "Feed_Calculation.xlsx";
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      showNotice("تم تجهيز ملف Excel.");
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  if (!initData && !pagesDemo) {
    showNotice("افتح التطبيق من زر «افتح التطبيق» داخل البوت.", true);
    return;
  }
  const initialState = pagesDemo
    ? fetch("./demo-feeds.json").then((response) => response.json()).then((feeds) => {
      const saved = JSON.parse(localStorage.getItem(demoStorageKey) || "{}");
      state = makeDemoState(feeds, saved);
      return state;
    })
    : api("/api/state").then((response) => response.json());
  initialState
    .then((loadedState) => {
      state = loadedState;
      render();
      if (pagesDemo) showNotice("نسخة Pages تجريبية: البيانات تُحفظ في هذا المتصفح فقط.");
    })
    .catch((error) => showNotice(error.message, true));
})();
