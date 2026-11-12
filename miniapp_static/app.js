(() => {
  const tg = window.Telegram?.WebApp;
  const initData = tg?.initData || window.__MINIAPP_DEMO_INIT_DATA__ || "";
  const $ = (id) => document.getElementById(id);
  const fmt = (value, digits = 2) => Number(value || 0).toLocaleString("ar-EG", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
  let state;
  let noticeTimer;

  tg?.ready();
  tg?.expand();

  function showNotice(message, error = false) {
    const notice = $("notice");
    notice.textContent = message;
    notice.classList.toggle("error", error);
    notice.hidden = false;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { notice.hidden = true; }, 5000);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
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
      link.download = "Feed_Calculation.xlsx";
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

  if (!initData) {
    showNotice("افتح التطبيق من زر «افتح التطبيق» داخل البوت.", true);
    return;
  }
  api("/api/state")
    .then(async (response) => {
      state = await response.json();
      render();
    })
    .catch((error) => showNotice(error.message, true));
})();
