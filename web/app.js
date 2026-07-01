// Household Budget — single-page front-end. Talks to the local FastAPI backend.
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

const api = async (path, opts = {}) => {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const txt = await res.text();
    throw new Error(txt || res.statusText);
  }
  return res.json();
};

const money = (n) =>
  n == null ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD" });
const signClass = (n) => (n > 0 ? "pos" : n < 0 ? "neg" : "");

// ---------------------------------------------------------------- navigation
$$(".nav-item").forEach((btn) =>
  btn.addEventListener("click", () => {
    $$(".nav-item").forEach((b) => b.classList.remove("active"));
    $$(".view").forEach((v) => v.classList.remove("active"));
    btn.classList.add("active");
    $(`#view-${btn.dataset.view}`).classList.add("active");
    VIEWS[btn.dataset.view]?.();
  })
);

// ---------------------------------------------------------------- status
async function loadStatus() {
  try {
    const s = await api("/status");
    const c = await api("/chat/health");
    $("#status").innerHTML =
      `Plaid: <b>${s.plaid_configured ? s.plaid_env : "not set"}</b><br>` +
      `AI: <b>${c.claude_available ? s.claude_model : "offline"}</b>`;
    const tg = $("#tg-status");
    if (tg) {
      if (!s.telegram_configured) {
        tg.textContent = "not set up";
      } else if (s.telegram_allowed_count === 0) {
        tg.textContent = "pairing — add your chat ID";
        tg.className = "chip bad";
      } else {
        tg.textContent = `active · ${s.telegram_allowed_count} user(s)`;
        tg.className = "chip ok";
      }
    }
  } catch (e) {
    $("#status").textContent = "backend offline";
  }
}

// ---------------------------------------------------------------- dashboard
async function loadDashboard() {
  const acc = await api("/accounts");
  const nw = acc.net_worth;
  $("#dash-reminder").innerHTML = staleBanner(acc.accounts);
  $("#networth-cards").innerHTML = `
    ${card("Net Worth", money(nw.net_worth), signClass(nw.net_worth))}
    ${card("Assets", money(nw.assets))}
    ${card("Liabilities", money(nw.liabilities))}`;

  const sum = await api("/budgets/summary");
  $("#month-cards").innerHTML = `
    ${card("Income", money(sum.income))}
    ${card("Expenses", money(sum.expenses))}
    ${card("Net", money(sum.net), signClass(sum.net))}`;
  $("#month-categories").innerHTML = sum.by_category.length
    ? `<table><thead><tr><th>Category</th><th class="num">Spent</th><th class="num">#</th></tr></thead><tbody>${sum.by_category
        .map((c) => `<tr><td>${c.category}</td><td class="num">${money(c.total)}</td><td class="num">${c.n}</td></tr>`)
        .join("")}</tbody></table>`
    : `<p class="muted">No transactions yet — load sample data or connect a bank in Setup.</p>`;

  const cf = (await api("/budgets/cash-flow?months=6")).cash_flow;
  const max = Math.max(1, ...cf.flatMap((m) => [m.income, m.expenses]));
  $("#cashflow-chart").innerHTML = cf.length
    ? cf
        .map(
          (m) => `<div class="cf-row"><span class="label">${m.month}</span>
        <div class="cf-bars">
          <div class="inc" style="width:${(m.income / max) * 60}%" title="Income ${money(m.income)}"></div>
          <div class="exp" style="width:${(m.expenses / max) * 60}%" title="Expenses ${money(m.expenses)}"></div>
          <span class="muted">${money(m.net)}</span>
        </div></div>`
        )
        .join("")
    : `<p class="muted">No history yet.</p>`;

  $("#accounts-list").innerHTML = acc.accounts.length
    ? `<table><thead><tr><th>Account</th><th>Type</th><th class="num">Balance</th></tr></thead><tbody>${acc.accounts
        .map(
          (a) =>
            `<tr><td>${esc(a.display_name || a.name || "—")}${a.mask ? " ••" + a.mask : ""}</td><td>${a.subtype || a.type}</td><td class="num">${money(a.current_balance)}</td></tr>`
        )
        .join("")}</tbody></table>`
    : `<p class="muted">No accounts connected.</p>`;
}
const card = (label, value, cls = "") =>
  `<div class="card"><div class="label">${label}</div><div class="value ${cls}">${value}</div></div>`;

// ---------------------------------------------------------------- accounts
const LIABILITY_TYPES = ["credit", "loan"];
let accountsById = {}; // latest accounts keyed by id, for the edit popup

// A banner nudging the user to refresh manual balances/asset values that have
// gone stale (manual accounts don't auto-update the way Plaid ones do).
function staleBanner(accounts) {
  const stale = accounts.filter((a) => a.is_stale);
  if (!stale.length) return "";
  const names = stale.map((a) => esc(a.display_name || a.name)).join(", ");
  const s = stale.length > 1;
  return `<div class="reminder">⚠ ${stale.length} account value${s ? "s" : ""} may be out of date
    (${names}). Update ${s ? "them" : "it"} on the Accounts tab to keep net worth accurate.</div>`;
}

async function loadAccounts() {
  const d = await api("/accounts");
  const nw = d.net_worth;
  $("#accounts-reminder").innerHTML = staleBanner(d.accounts);
  $("#accounts-networth").innerHTML =
    `${card("Net Worth", money(nw.net_worth), signClass(nw.net_worth))}
     ${card("Assets", money(nw.assets))}
     ${card("Liabilities", money(nw.liabilities))}` +
    (nw.real_assets ? card("Owned Assets", money(nw.real_assets)) : "");

  // Populate the "financed by" dropdown on the asset form with current loans.
  const loanSel = $("#asset-loan-select");
  if (loanSel) {
    const loans = d.accounts.filter((a) => LIABILITY_TYPES.includes(a.type));
    loanSel.innerHTML =
      `<option value="">Not financed / no loan</option>` +
      loans
        .map((a) => `<option value="${a.id}">${esc(a.display_name || a.name)}${a.mask ? " ••" + a.mask : ""}</option>`)
        .join("");
  }

  // Keep the latest accounts so the edit popup can look one up by id.
  accountsById = {};
  d.accounts.forEach((a) => (accountsById[a.id] = a));

  $("#accounts-manage").innerHTML = d.accounts.length
    ? `<table><thead><tr><th>Account</th><th>Type</th><th>Source</th>
         <th class="num">Balance</th><th>APR · Payment/mo</th><th>Updated</th><th></th></tr></thead>
       <tbody>${d.accounts
         .map((a) => {
           const liab = LIABILITY_TYPES.includes(a.type);
           const isAsset = a.type === "asset";
           const bal = liab ? `<span class="neg">${money(a.current_balance)}</span>` : money(a.current_balance);
           // Name: custom name (or bank name) on top; show the original underneath
           // only when a custom name overrides it, so it's clear what it maps to.
           const name = a.display_name || a.name || "—";
           const nameCell =
             `<strong>${esc(name)}</strong>${a.mask ? ` <span class="muted">••${a.mask}</span>` : ""}` +
             (a.custom_name && a.name && a.custom_name !== a.name
               ? `<div class="muted" style="font-size:12px">${esc(a.name)}</div>`
               : "") +
             (a.equity != null
               ? `<div class="muted" style="font-size:12px">Equity ${money(a.equity)} · financed by ${esc(a.linked_name)}</div>`
               : "");
           const typeCell = isAsset
             ? `${esc(a.subtype || "asset")} <span class="chip">asset</span>`
             : `${esc(a.subtype || a.type)}${liab ? ' <span class="chip">owed</span>' : ""}`;
           // Read-only terms summary; editing happens in the popup.
           const termsCell = liab
             ? `${a.interest_rate != null ? a.interest_rate + "%" : '<span class="muted">no APR</span>'}
                · ${a.monthly_payment != null ? "$" + a.monthly_payment + "/mo" : '<span class="muted">no payment</span>'}`
             : `<span class="muted">—</span>`;
           const updatedCell =
             `${(a.updated_at || "").slice(0, 10)}` +
             (a.is_stale ? ` <span class="chip warn">stale ${a.stale_days}d</span>` : "");
           const sourceChip = a.is_manual
             ? `<span class="chip">manual</span>`
             : `<span class="chip ok">Plaid</span>`;
           return `<tr>
             <td>${nameCell}</td>
             <td>${typeCell}</td>
             <td>${sourceChip}</td>
             <td class="num">${bal}</td>
             <td>${termsCell}</td>
             <td class="muted">${updatedCell}</td>
             <td><button class="secondary" title="Edit account" onclick="openAccountEdit('${a.id}')">✎ Edit</button></td>
           </tr>`;
         })
         .join("")}</tbody></table>`
    : `<p class="muted">No accounts yet. Connect a bank in Setup, or add a manual account below.</p>`;

  renderDebtPlan(await api("/accounts/debt-plan"));
}

// Avalanche-ranked payoff guidance: highest-APR debt first. With a monthly payment
// set, payoff date + TRUE interest (12-mo and to-clear) are amortized, not flat×12.
function renderDebtPlan(plan) {
  const el = $("#debt-plan");
  if (!plan.debts.length) {
    el.innerHTML = `<p class="muted">No outstanding debt. 🎉</p>`;
    return;
  }
  const notes = [];
  if (plan.missing_rates)
    notes.push(`Add an APR to ${plan.missing_rates} debt${plan.missing_rates > 1 ? "s" : ""} (they're listed last and have no interest estimate).`);
  if (plan.missing_payments)
    notes.push(`Set a monthly payment on ${plan.missing_payments} debt${plan.missing_payments > 1 ? "s" : ""} to get a payoff date and accurate interest — without it we can only show this month's interest.`);
  const warn = notes.length ? `<div class="reminder">${notes.join("<br>")}</div>` : "";

  // "Interest next 12 mo" only covers debts we could project (payment set).
  const twelveLabel = plan.projected_count < plan.debts.length
    ? `Interest next 12mo*`
    : `Interest next 12mo`;

  el.innerHTML =
    warn +
    `<div class="cards">
       ${card("Total Debt", money(plan.total_debt))}
       ${card("Interest this month", money(plan.total_monthly_interest))}
       ${card(twelveLabel, money(plan.total_interest_next_12mo))}
     </div>
     ${plan.projected_count < plan.debts.length ? `<p class="muted" style="font-size:12px">*only counts the ${plan.projected_count} debt(s) with a monthly payment set.</p>` : ""}
     <table><thead><tr><th>Pay order</th><th>Debt</th><th class="num">Balance</th>
       <th class="num">APR</th><th class="num">Payment/mo</th>
       <th class="num">Payoff</th><th class="num">Interest to clear</th></tr></thead>
     <tbody>${plan.debts
       .map((d) => {
         const apr = d.apr != null ? d.apr.toFixed(2) + "%" : '<span class="chip warn">no rate</span>';
         const pay = d.monthly_payment != null ? money(d.monthly_payment) : '<span class="muted">—</span>';
         let payoff, interest;
         if (d.never_pays_off) {
           payoff = '<span class="chip bad">never*</span>';
           interest = '<span class="chip bad">∞</span>';
         } else if (d.payoff_date) {
           payoff = `${d.payoff_date} <span class="muted">(${d.payoff_months} mo)</span>`;
           interest = money(d.payoff_total_interest);
         } else {
           payoff = '<span class="muted">add payment</span>';
           interest = '<span class="muted">—</span>';
         }
         return `<tr>
           <td>#${d.payoff_order}</td>
           <td>${esc(d.name)}${d.mask ? " ••" + d.mask : ""}</td>
           <td class="num">${money(d.balance)}</td>
           <td class="num">${apr}</td>
           <td class="num">${pay}</td>
           <td class="num">${payoff}</td>
           <td class="num">${interest}</td>
         </tr>`;
       })
       .join("")}</tbody></table>
     ${plan.debts.some((d) => d.never_pays_off) ? `<p class="muted" style="font-size:12px">*payment doesn't cover the monthly interest — the balance won't go down. Increase the payment.</p>` : ""}`;
}

// One popup gathers every edit available for a row: custom name (any account),
// balance (manual only), APR + monthly payment (credit/loan), financing link
// (assets), and delete (manual only). Replaces the old per-cell inline controls.
window.closeAccountModal = () => {
  $("#modal-root").innerHTML = "";
};
window.openAccountEdit = (id) => {
  const a = accountsById[id];
  if (!a) return;
  const liab = LIABILITY_TYPES.includes(a.type);
  const isAsset = a.type === "asset";

  const balanceField = a.is_manual
    ? `<div class="field">
         <label>Balance</label>
         <input id="m-bal" type="number" step="0.01" value="${a.current_balance ?? 0}" />
       </div>`
    : `<div class="field">
         <label>Balance</label>
         <input value="${money(a.current_balance)}" disabled />
         <div class="hint">Plaid-synced — updates automatically on sync.</div>
       </div>`;

  const termsField = liab
    ? `<div class="field">
         <label>APR &amp; monthly payment</label>
         <div class="pair">
           <div><input id="m-apr" type="number" step="0.01" min="0" placeholder="APR %" value="${a.interest_rate ?? ""}" /></div>
           <div><input id="m-pay" type="number" step="1" min="0" placeholder="$ / month" value="${a.monthly_payment ?? ""}" /></div>
         </div>
         <div class="hint">Payment drives the payoff date &amp; true-interest math.</div>
       </div>`
    : "";

  const loans = Object.values(accountsById).filter(
    (x) => LIABILITY_TYPES.includes(x.type) && x.id !== id
  );
  const linkField = isAsset
    ? `<div class="field">
         <label>Financed by (loan)</label>
         <select id="m-link">
           <option value="">Not financed / no loan</option>
           ${loans
             .map(
               (l) =>
                 `<option value="${l.id}" ${l.id === a.linked_account_id ? "selected" : ""}>${esc(
                   l.display_name || l.name
                 )}${l.mask ? " ••" + l.mask : ""}</option>`
             )
             .join("")}
         </select>
         <div class="hint">Links this asset to its loan to show equity (value − owed).</div>
       </div>`
    : "";

  const deleteBtn = a.is_manual
    ? `<button class="danger" onclick="deleteAccountFromModal('${id}')">Delete</button>`
    : "";

  $("#modal-root").innerHTML = `
    <div class="modal-overlay" onclick="if(event.target===this)closeAccountModal()">
      <div class="modal">
        <h3>Edit account</h3>
        <div class="muted">${esc(a.name || "—")}${a.mask ? " ••" + a.mask : ""} · ${a.is_manual ? "manual" : "Plaid"}</div>
        <div class="field">
          <label>Display name</label>
          <input id="m-name" type="text" value="${esc(a.custom_name || "")}" placeholder="${esc(a.name || "")}" />
          <div class="hint">Leave blank to use the bank's name${a.name ? ` (“${esc(a.name)}”)` : ""}.</div>
        </div>
        ${balanceField}
        ${termsField}
        ${linkField}
        <div class="actions">
          ${deleteBtn}
          <span class="spacer"></span>
          <button class="secondary" onclick="closeAccountModal()">Cancel</button>
          <button onclick="saveAccountEdit('${id}')">Save</button>
        </div>
      </div>
    </div>`;
};
window.deleteAccountFromModal = async (id) => {
  const a = accountsById[id];
  if (!confirm(`Delete manual account "${a ? a.display_name || a.name : id}"?`)) return;
  await api(`/accounts/${id}`, { method: "DELETE" });
  closeAccountModal();
  loadAccounts();
};
window.saveAccountEdit = async (id) => {
  const a = accountsById[id];
  if (!a) return;
  const liab = LIABILITY_TYPES.includes(a.type);

  // custom_name is always present in the body so "" clears it (reverts to bank name).
  const body = { custom_name: $("#m-name").value };
  if (a.is_manual) {
    const bal = parseFloat($("#m-bal").value);
    if (!Number.isNaN(bal)) body.current_balance = bal;
    if (a.type === "asset") body.linked_account_id = $("#m-link").value || "";
  }
  await api(`/accounts/${id}`, { method: "PATCH", body: JSON.stringify(body) });

  // APR / payment live on a separate endpoint (allowed on Plaid debts too).
  if (liab) {
    const aprRaw = $("#m-apr").value;
    const payRaw = $("#m-pay").value;
    const apr = aprRaw === "" ? null : parseFloat(aprRaw);
    const pay = payRaw === "" ? null : parseFloat(payRaw);
    if ((apr === null || !Number.isNaN(apr)) && (pay === null || !Number.isNaN(pay))) {
      await api(`/accounts/${id}/terms`, {
        method: "PATCH",
        body: JSON.stringify({ interest_rate: apr, monthly_payment: pay }),
      });
    }
  }
  closeAccountModal();
  loadAccounts();
};
$("#manual-account-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const [type, subtype] = f.kind.value.split("|");
  await api("/accounts/manual", {
    method: "POST",
    body: JSON.stringify({
      name: f.name.value,
      type,
      subtype,
      current_balance: parseFloat(f.current_balance.value) || 0,
    }),
  });
  f.reset();
  loadAccounts();
});

$("#asset-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  await api("/accounts/manual", {
    method: "POST",
    body: JSON.stringify({
      name: f.name.value,
      type: "asset",
      subtype: f.subtype.value,
      current_balance: parseFloat(f.current_balance.value) || 0,
      linked_account_id: f.linked_account_id.value || null,
    }),
  });
  f.reset();
  loadAccounts();
});

// ---------------------------------------------------------------- budgets
let budgetRows = [];
async function loadBudgets() {
  const [{ budgets, surplus, credit_card }, { categories }] = await Promise.all([
    api("/budgets"),
    api("/transactions/categories"),
  ]);
  budgetRows = budgets;
  const expenseCats = categories.filter((c) => c.kind === "expense");

  // Single "cash going out" figure = planned spending (sum of expense limits) +
  // planned credit-card paydown (the CC-payment plan target). One number to track.
  const totalBudget = budgets.reduce((s, b) => s + (b.limit || 0), 0);
  const cc = credit_card || {};
  const plannedOut = totalBudget + (cc.target || 0);
  $("#budgets-surplus").innerHTML =
    card("Surplus this month", money(surplus), signClass(surplus)) +
    card("Planned cash out", money(plannedOut));
  renderCcPayPlan(cc);

  const rows = budgets
    .map((b, i) => {
      const pct = b.pct == null ? 0 : Math.min(100, b.pct);
      return `<tr>
        <td>${b.category}</td>
        <td class="num"><input id="blim-${i}" type="number" step="0.01" value="${b.limit}" style="width:110px" /></td>
        <td class="num"><a href="#" class="link" onclick="showBudgetTxns(${b.category_id});return false" title="Show the transactions in this total">${money(b.actual)}</a></td>
        <td style="width:200px"><div class="bar ${b.over ? "over" : ""}"><span style="width:${pct}%"></span></div></td>
        <td class="num ${b.remaining < 0 ? "neg" : ""}">${money(b.remaining)}</td>
        <td><button onclick="saveBudget(${i})">Save</button>
            <button class="secondary" onclick="deleteBudget(${i})" title="Remove this budget">✕</button></td>
      </tr>`;
    })
    .join("");

  $("#budgets-table").innerHTML = `
    <table><thead><tr><th>Category</th><th class="num">Limit</th><th class="num">Spent</th><th>Progress</th><th class="num">Left</th><th></th></tr></thead>
    <tbody>${rows || `<tr><td colspan="6" class="muted">No budgets set yet.</td></tr>`}</tbody></table>
    <div class="panel"><h2>Set / update a budget</h2>
      <div class="form-row">
        <select id="budget-cat">${expenseCats.map((c) => `<option value="${c.id}">${c.name}</option>`).join("")}</select>
        <input id="budget-amt" type="number" step="0.01" placeholder="Monthly limit $" />
        <button id="budget-save">Save</button>
      </div>
    </div>`;

  $("#budget-save").addEventListener("click", async () => {
    const id = $("#budget-cat").value;
    const amt = parseFloat($("#budget-amt").value);
    if (!(amt >= 0)) return;
    await api(`/budgets/${id}`, { method: "PUT", body: JSON.stringify({ monthly_limit: amt }) });
    loadBudgets();
  });
}
window.saveBudget = async (i) => {
  const b = budgetRows[i];
  const amt = parseFloat($(`#blim-${i}`).value);
  if (!b || !(amt >= 0)) return;
  await api(`/budgets/${b.category_id}`, { method: "PUT", body: JSON.stringify({ monthly_limit: amt }) });
  loadBudgets();
};
window.deleteBudget = async (i) => {
  const b = budgetRows[i];
  if (!b || !confirm(`Remove the budget for "${b.category}"?`)) return;
  await api(`/budgets/${b.category_id}`, { method: "DELETE" });
  loadBudgets();
};

// Credit-card payment plan: money sent to Plaid cards this month vs a target you
// set. It's a transfer (card purchases are already in the budgets), so it's shown
// here as its own paydown figure rather than as spending.
let ccPayCategoryId = null;
function renderCcPayPlan(cc) {
  ccPayCategoryId = cc.category_id;
  if (!cc.category_id) {
    // Category not present yet (server not restarted since the migration).
    $("#budgets-ccpay").innerHTML = "";
    return;
  }
  const actual = cc.actual || 0;
  const target = cc.target;
  const pct = target ? Math.min(100, Math.round((actual / target) * 100)) : 0;
  const bar = target
    ? `<div class="bar ${cc.over ? "over" : ""}" style="max-width:320px;margin-top:6px">
         <span style="width:${pct}%"></span></div>`
    : "";
  const status = target
    ? `<b>${money(actual)}</b> of <b>${money(target)}</b> planned · ${
        cc.over
          ? `<span class="neg">${money(actual - target)} over</span>`
          : `${money(target - actual)} left`
      }`
    : `<b>${money(actual)}</b> sent so far · no monthly plan set`;
  $("#budgets-ccpay").innerHTML = `
    <div class="panel" style="margin-bottom:14px">
      <h2>Credit card payments</h2>
      <p class="muted" style="margin-top:0">
        Money sent to your Plaid credit cards this month. This is a transfer, not
        spending — your card purchases are already counted in the budgets below — so
        it tracks paydown without double-counting. Set what you plan to send monthly.</p>
      <div>${status}</div>
      ${bar}
      <div class="form-row" style="margin-top:12px">
        <input id="ccpay-target" type="number" step="0.01" placeholder="Planned $ / month"
               value="${target != null ? target : ""}" style="width:180px" />
        <button id="ccpay-save">Save plan</button>
      </div>
    </div>`;
  $("#ccpay-save").addEventListener("click", saveCcPayTarget);
}
async function saveCcPayTarget() {
  const raw = $("#ccpay-target").value.trim();
  if (!ccPayCategoryId) return;
  if (raw === "") {
    // Blank clears the plan.
    await api(`/budgets/${ccPayCategoryId}`, { method: "DELETE" });
  } else {
    const amt = parseFloat(raw);
    if (!(amt >= 0)) return;
    await api(`/budgets/${ccPayCategoryId}`, {
      method: "PUT",
      body: JSON.stringify({ monthly_limit: amt }),
    });
  }
  loadBudgets();
}

// Drill-down: click a budget's Spent figure to see the exact transactions in it.
// The listed amounts sum to the Spent total (money-in nets down, same as the total).
window.closeBudgetModal = () => {
  $("#modal-root").innerHTML = "";
};
window.showBudgetTxns = async (categoryId) => {
  let data;
  try {
    data = await api(`/budgets/category-transactions?category_id=${categoryId}`);
  } catch (e) {
    alert("Couldn't load transactions: " + e.message);
    return;
  }
  const rows = data.transactions.length
    ? data.transactions
        .map(
          (t) => `<tr>
            <td>${esc(t.date)}</td>
            <td>${esc(t.name || t.merchant_name || "—")}${
              t.pending ? ' <span class="muted">(pending)</span>' : ""
            }<div class="muted" style="font-size:11px">${esc(t.account || "")}${
              t.rule_pattern ? ` · rule “${esc(t.rule_pattern)}”` : ""
            }</div></td>
            <td class="num ${t.amount < 0 ? "pos" : ""}">${money(t.amount)}</td>
          </tr>`
        )
        .join("")
    : `<tr><td colspan="3" class="muted">No transactions this month.</td></tr>`;

  $("#modal-root").innerHTML = `
    <div class="modal-overlay" onclick="if(event.target===this)closeBudgetModal()">
      <div class="modal" style="width:640px">
        <h3>${esc(data.category)} — ${esc(data.month)}</h3>
        <div class="muted">${data.count} transaction${data.count === 1 ? "" : "s"} ·
          Spent <b>${money(data.actual)}</b>${
            data.transactions.some((t) => t.amount < 0)
              ? ' <span class="muted">(money-in, shown in green, nets the total down)</span>'
              : ""
          }</div>
        <div style="max-height:60vh;overflow:auto;margin-top:8px">
          <table><thead><tr><th>Date</th><th>Description</th><th class="num">Amount</th></tr></thead>
          <tbody>${rows}</tbody></table>
        </div>
        <div class="actions">
          <span class="spacer"></span>
          <button onclick="closeBudgetModal()">Close</button>
        </div>
      </div>
    </div>`;
};

// ---------------------------------------------------------------- goals
let goalsById = {};
let goalAccounts = []; // accounts available to bind a goal to

function acctLabel(a) {
  // Prefer the resolved display name (custom name override) wherever it's available.
  const nm = a.display_name || a.name;
  return `${nm}${a.mask ? ` ••${a.mask}` : ""}`;
}
// Checkboxes for binding accounts to a goal. `selectedIds` pre-checks bound ones.
function accountChecklist(selectedIds) {
  const sel = new Set(selectedIds || []);
  if (!goalAccounts.length) return `<span class="muted">No accounts yet.</span>`;
  return goalAccounts
    .map(
      (a) =>
        `<label class="acct-pick"><input type="checkbox" value="${esc(a.id)}" ${
          sel.has(a.id) ? "checked" : ""
        } /> ${esc(acctLabel(a))} <span class="muted">(${esc(a.type)})</span></label>`
    )
    .join("");
}
function checkedAccountIds(container) {
  if (!container) return [];
  return Array.from(container.querySelectorAll("input[type=checkbox]:checked")).map((c) => c.value);
}

async function loadGoals() {
  const [data, acc] = await Promise.all([api("/goals"), api("/accounts")]);
  goalAccounts = acc.accounts || [];
  goalsById = {};
  data.goals.forEach((g) => (goalsById[g.id] = g));
  $("#surplus-banner").innerHTML =
    `Average monthly surplus: <b class="${signClass(data.monthly_surplus)}">${money(data.monthly_surplus)}</b>`;
  const picker = $("#goal-accounts");
  if (picker) picker.innerHTML = accountChecklist([]);

  $("#goals-list").innerHTML = data.goals.length
    ? data.goals
        .map((g) => {
          const pct = g.target_amount > 0 ? Math.min(100, (g.current_amount / g.target_amount) * 100) : 0;
          const isPayoff = g.mode === "payoff";
          const progress = isPayoff
            ? `${money(g.current_amount)} paid of ${money(g.target_amount)} · ${money(g.remaining)} left`
            : `${money(g.current_amount)} of ${money(g.target_amount)}`;
          const badge = g.complete
            ? `<span class="chip ok">✓ complete</span>`
            : g.on_track === null
            ? ""
            : g.on_track
            ? `<span class="chip ok">on track</span>`
            : `<span class="chip bad">behind</span>`;
          const payoffMeta = isPayoff
            ? g.apr_known
              ? ` (pay-off @ ${g.apr}% APR)`
              : ` (pay-off — <span class="warn">add an APR</span> on these accounts for an interest-aware estimate)`
            : "";
          const tracking = g.accounts.length
            ? `<div class="muted">Tracking: ${g.accounts.map((a) => esc(a.display_name || a.name)).join(", ")}${payoffMeta}</div>`
            : "";
          const interestNote =
            isPayoff && !g.complete && g.interest_to_payoff != null
              ? ` · ~${money(g.interest_to_payoff)} interest to clear`
              : "";
          const footer = g.complete
            ? `<div class="muted">✓ Completed <b>${g.completed_at || ""}</b></div>`
            : `<div class="muted">Projected done: <b>${g.projected_date || "—"}</b>
                ${g.target_date ? ` · target ${g.target_date}` : ""}
                ${g.months_to_complete != null ? ` · ~${g.months_to_complete} mo` : ""}${interestNote}</div>`;
          return `<div class="goal" id="goal-${g.id}">
            <div class="top"><span class="name">${esc(g.name)}</span>
              <span><button class="secondary" onclick="startEditGoal(${g.id})">Edit</button>
                <button class="secondary" onclick="deleteGoal(${g.id})">✕</button></span></div>
            <div class="muted">${progress} ${badge}</div>
            ${tracking}
            <div class="bar" style="margin:8px 0"><span style="width:${pct}%"></span></div>
            ${footer}
          </div>`;
        })
        .join("")
    : `<p class="muted">No goals yet. Add one below.</p>`;
}
// Turn a goal card into an inline editor.
window.startEditGoal = (id) => {
  const g = goalsById[id];
  const div = document.getElementById(`goal-${id}`);
  if (!g || !div) return;
  div.classList.add("editing");
  const boundIds = (g.accounts || []).map((a) => a.id);
  div.innerHTML = `
    <div class="form-row" style="flex-wrap:wrap">
      <input id="ge-name-${id}" value="${esc(g.name)}" placeholder="Name" />
      <input id="ge-target-${id}" type="number" step="0.01" value="${g.target_amount}" placeholder="Target $" />
      <input id="ge-current-${id}" type="number" step="0.01" value="${g.current_amount}" placeholder="Saved so far $" />
      <input id="ge-date-${id}" type="date" value="${g.target_date || ""}" />
      <input id="ge-priority-${id}" type="number" value="${g.priority ?? 100}" placeholder="Priority (1=first)" />
      <button onclick="saveGoalEdit(${id})">Save</button>
      <button class="secondary" onclick="loadGoals()">Cancel</button>
    </div>
    <div class="muted" style="margin-top:6px">Tracked accounts (credit/loan = pay-off goal):</div>
    <div id="ge-acct-${id}" class="acct-picklist">${accountChecklist(boundIds)}</div>`;
  $(`#ge-name-${id}`)?.focus();
};
window.saveGoalEdit = async (id) => {
  const name = $(`#ge-name-${id}`).value.trim();
  const target = parseFloat($(`#ge-target-${id}`).value);
  if (!name || !(target > 0)) return;
  const body = {
    name,
    target_amount: target,
    current_amount: parseFloat($(`#ge-current-${id}`).value) || 0,
    target_date: $(`#ge-date-${id}`).value || null,
    priority: parseInt($(`#ge-priority-${id}`).value) || 100,
    account_ids: checkedAccountIds(document.getElementById(`ge-acct-${id}`)),
  };
  await api(`/goals/${id}`, { method: "PUT", body: JSON.stringify(body) });
  loadGoals();
};
window.deleteGoal = async (id) => {
  await api(`/goals/${id}`, { method: "DELETE" });
  loadGoals();
};
$("#goal-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const accountIds = checkedAccountIds($("#goal-accounts"));
  const target = parseFloat(f.target_amount.value) || 0;
  if (!target && !accountIds.length) {
    alert("Enter a target amount, or bind an account to track.");
    return;
  }
  const body = {
    name: f.name.value,
    target_amount: target,
    current_amount: parseFloat(f.current_amount.value) || 0,
    target_date: f.target_date.value || null,
    priority: parseInt(f.priority.value) || 100,
    account_ids: accountIds,
  };
  await api("/goals", { method: "POST", body: JSON.stringify(body) });
  f.reset();
  loadGoals();
});

// ---------------------------------------------------------------- recurring
let recurringList = [];
async function loadRecurring() {
  const [d, { dismissed }] = await Promise.all([
    api("/recurring"),
    api("/recurring/dismissed"),
  ]);
  recurringList = d.recurring;
  $("#recurring-cards").innerHTML = `
    ${card("Monthly recurring", money(d.monthly_total))}
    ${card("Annual recurring", money(d.annual_total))}
    ${card("Active subscriptions/bills", String(d.active_count))}`;
  $("#recurring-table").innerHTML = d.recurring.length
    ? `<table><thead><tr><th>Merchant</th><th>Cadence</th><th class="num">Amount</th>
         <th>Category</th><th>Last</th><th>Next due</th><th class="num">Monthly</th><th></th></tr></thead>
       <tbody>${d.recurring
         .map(
           (r, i) => `<tr style="${r.active ? "" : "opacity:.5"}">
        <td>${esc(r.merchant || "—")} ${r.active ? "" : '<span class="chip">inactive?</span>'}</td>
        <td>${r.cadence}</td>
        <td class="num">${money(r.amount)} ${r.amount_varies ? '<span class="chip">~varies</span>' : ""}</td>
        <td>${esc(r.category || "—")}</td>
        <td>${r.last_date}</td>
        <td>${r.next_expected}</td>
        <td class="num">${money(r.monthly_equivalent)}</td>
        <td><button class="secondary" onclick="dismissRecurring(${i})" title="Remove (e.g. cancelled)">✕</button></td>
      </tr>`
         )
         .join("")}</tbody></table>`
    : `<p class="muted">No recurring charges detected yet. Connect a bank or load sample data — detection needs at least 3 occurrences of a charge.</p>`;

  const panel = $("#dismissed-panel");
  if (dismissed.length) {
    panel.style.display = "";
    $("#dismissed-table").innerHTML = `<table><tbody>${dismissed
      .map(
        (x) => `<tr><td>${esc(x.label || x.key)}</td>
        <td class="muted">dismissed ${x.dismissed_at}</td>
        <td><button class="secondary" onclick="restoreRecurring('${encodeURIComponent(x.key)}')">Restore</button></td></tr>`
      )
      .join("")}</tbody></table>`;
  } else {
    panel.style.display = "none";
  }
}
window.dismissRecurring = async (i) => {
  const r = recurringList[i];
  if (!confirm(`Remove "${r.merchant}" from recurring? It'll reappear if it's charged again.`)) return;
  await api("/recurring/dismiss", {
    method: "POST",
    body: JSON.stringify({ key: r.key, label: r.merchant }),
  });
  loadRecurring();
};
window.restoreRecurring = async (encKey) => {
  await api("/recurring/restore", {
    method: "POST",
    body: JSON.stringify({ key: decodeURIComponent(encKey) }),
  });
  loadRecurring();
};

// ---------------------------------------------------------------- transactions
const TXN_PAGE = 100;
let txnOffset = 0;
async function loadTransactions() {
  const query = $("#txn-search").value.trim();
  const month = $("#txn-month").value;
  // A search spans the last 6 months (server-side) and ignores the month filter.
  const parts = [`limit=${TXN_PAGE}`, `offset=${txnOffset}`];
  if (query) parts.push(`q=${encodeURIComponent(query)}`);
  else if (month) parts.push(`month=${month}`);
  const [{ transactions, total, offset, limit }, { categories }] = await Promise.all([
    api(`/transactions?${parts.join("&")}`),
    api("/transactions/categories"),
  ]);
  renderTxnPager(total, offset, limit, transactions.length);
  const opts = (sel) =>
    categories.map((c) => `<option value="${c.id}" ${c.id === sel ? "selected" : ""}>${c.name}</option>`).join("");
  $("#transactions-table").innerHTML = transactions.length
    ? `<table><thead><tr><th>Date</th><th>Description</th><th>Category</th><th class="num">Amount</th></tr></thead>
      <tbody>${transactions
        .map(
          (t) => `<tr>
        <td>${t.date}</td>
        <td>${t.name || ""}${t.pending ? ' <span class="chip">pending</span>' : ""}</td>
        <td><select onchange="setCat('${t.id}', this.value)">${opts(t.category_id)}</select>${
            t.category_rule_id
              ? ` <a href="#" class="rule-chip" title="Set by rule — click to edit" onclick="gotoRule(${t.category_rule_id});return false;">⚙ ${esc(t.rule_pattern || "rule")}</a>`
              : ""
          }</td>
        <td class="num ${t.amount < 0 ? "pos" : ""}">${money(-t.amount)}</td>
      </tr>`
        )
        .join("")}</tbody></table>`
    : query
    ? `<p class="muted">No transactions in the last 6 months match “${esc(query)}”.</p>`
    : `<p class="muted">No transactions for this period.</p>`;
}
// Show "X–Y of N" with Prev/Next; hidden when everything fits on one page.
function renderTxnPager(total, offset, limit, count) {
  const el = $("#txn-pager");
  if (!el) return;
  if (!total || (offset === 0 && total <= limit)) {
    el.innerHTML = "";
    return;
  }
  const from = total === 0 ? 0 : offset + 1;
  const to = offset + count;
  const prevDis = offset <= 0 ? "disabled" : "";
  const nextDis = offset + limit >= total ? "disabled" : "";
  el.innerHTML =
    `<span class="muted">${from}–${to} of ${total}</span>` +
    `<button class="secondary" ${prevDis} onclick="txnPage(-1)">‹ Prev</button>` +
    `<button class="secondary" ${nextDis} onclick="txnPage(1)">Next ›</button>`;
}
window.txnPage = (dir) => {
  txnOffset = Math.max(0, txnOffset + dir * TXN_PAGE);
  loadTransactions();
};
window.setCat = async (id, cid) => {
  await api(`/transactions/${id}/category`, { method: "PATCH", body: JSON.stringify({ category_id: parseInt(cid) }) });
  // Reload so the rule chip clears (this is now a manual override).
  loadTransactions();
};
// Jump from a transaction to the rule that set it, and open it for editing.
window.gotoRule = async (ruleId) => {
  $$(".nav-item").forEach((b) => b.classList.remove("active"));
  $$(".view").forEach((v) => v.classList.remove("active"));
  $('.nav-item[data-view="rules"]').classList.add("active");
  $("#view-rules").classList.add("active");
  await loadRules();
  startEditRule(ruleId);
};
// Changing the filter resets to the first page.
$("#txn-month").addEventListener("change", () => {
  txnOffset = 0;
  loadTransactions();
});
let txnSearchTimer;
$("#txn-search").addEventListener("input", () => {
  clearTimeout(txnSearchTimer);
  txnSearchTimer = setTimeout(() => {
    txnOffset = 0;
    loadTransactions();
  }, 250); // debounce typing
});
$("#recategorize-btn").addEventListener("click", async () => {
  await api("/transactions/recategorize", { method: "POST" });
  loadTransactions();
});

// ---------------------------------------------------------------- rules
let uncatList = [];
// Sign-aware rules: a rule can apply to money-in, money-out, or either.
// (Plaid sign: amount > 0 = money out/spending, amount < 0 = money in/income.)
const DIR_LABEL = { any: "Any", in: "Money in", out: "Money out" };
function dirOptions(selected) {
  return ["any", "in", "out"]
    .map((d) => `<option value="${d}" ${d === selected ? "selected" : ""}>${DIR_LABEL[d]}</option>`)
    .join("");
}
function dirSelect(id, selected) {
  return `<select id="${id}">${dirOptions(selected)}</select>`;
}
function guessPattern(t) {
  if (t.merchant_name) return t.merchant_name.toLowerCase();
  // First couple of words of the description, minus numbers/punctuation.
  return (t.name || "")
    .toLowerCase()
    .replace(/[^a-z\s]/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .join(" ");
}
async function loadRules() {
  const [{ transactions }, { rules }, { categories }, { suggestions }, { mismatches }] = await Promise.all([
    api("/transactions/uncategorized"),
    api("/rules"),
    api("/transactions/categories"),
    api("/rules/suggestions"),
    api("/transactions/mismatches"),
  ]);
  uncatList = transactions;
  ruleCats = categories;
  $("#uncat-count").textContent = transactions.length;
  const opts = categories.map((c) => `<option value="${c.id}">${c.name}</option>`).join("");

  $("#uncategorized-table").innerHTML = transactions.length
    ? `<table><thead><tr><th>Date</th><th>Description</th><th>Keyword rule</th><th>Applies to</th><th>Category</th><th></th></tr></thead><tbody>${transactions
        .map(
          (t, i) => `<tr id="urow-${i}">
        <td>${t.date}</td>
        <td>${esc(t.name || t.merchant_name || "")}<br><span class="muted">${money(-t.amount)}</span>${
            t.pfc_category ? `<br><span class="muted">Plaid suggests: ${esc(t.pfc_category)}</span>` : ""
          }</td>
        <td><input id="pat-${i}" value="${esc(guessPattern(t))}" style="width:130px" onchange="saveSuggestion(${i})" /></td>
        <td>${dirSelect(`dir-${i}`, t.amount < 0 ? "in" : "out")}</td>
        <td><select id="cat-${i}" onchange="saveSuggestion(${i})">${opts}</select></td>
        <td><button onclick="applyRule(${i})">Add rule</button>
            <button class="secondary" onclick="setOnce(${i})">Set once</button></td>
      </tr>`
        )
        .join("")}</tbody></table>`
    : `<p class="muted">Nothing uncategorized 🎉</p>`;

  // Pre-fill the category dropdown from Plaid's PFC hint (a saved suggestion,
  // applied next, still wins if the user already tweaked the row).
  transactions.forEach((t, i) => {
    if (t.pfc_category_id) $(`#cat-${i}`).value = t.pfc_category_id;
  });
  // Restore saved AI/manual suggestions so they survive closing the window.
  transactions.forEach((t, i) => {
    const s = suggestions[t.id];
    if (!s) return;
    if (s.pattern) $(`#pat-${i}`).value = s.pattern;
    if (s.category_id) $(`#cat-${i}`).value = s.category_id;
  });

  renderMismatches(mismatches);
  renderRulesTable(rules);
}

// Transactions whose rule-assigned category KIND disagrees with Plaid's PFC.
function renderMismatches(mismatches) {
  $("#mismatch-count").textContent = mismatches.length || "";
  $("#mismatch-table").innerHTML = mismatches.length
    ? `<table><thead><tr><th>Date</th><th>Description</th><th>Rule set it to</th><th>Plaid says</th><th></th></tr></thead><tbody>${mismatches
        .map(
          (m) => `<tr id="mrow-${m.id}">
        <td>${m.date}</td>
        <td>${esc(m.name || m.merchant_name || "")}<br><span class="muted">${money(-m.amount)}</span></td>
        <td>${esc(m.category)}${m.rule_pattern ? `<br><span class="muted">rule: ${esc(m.rule_pattern)}</span>` : ""}</td>
        <td>${esc(m.pfc_category)}</td>
        <td><button onclick="acceptMismatch('${esc(m.id)}', ${m.pfc_category_id})">Use “${esc(m.pfc_category)}”</button>
            <button class="secondary" onclick="ignoreMismatch('${esc(m.id)}')">Ignore</button>
            ${m.rule_id ? `<button class="secondary" onclick="muteRuleMismatch(${m.rule_id})">Mute rule</button>` : ""}</td>
      </tr>`
        )
        .join("")}</tbody></table>`
    : `<p class="muted">No kind-level mismatches against Plaid's categories 🎉</p>`;
}

function dropMismatchRow(id) {
  const row = document.getElementById(`mrow-${id}`);
  if (row) row.remove();
  const n = parseInt($("#mismatch-count").textContent, 10);
  if (n) $("#mismatch-count").textContent = n - 1 || "";
}
async function refreshMismatches() {
  const { mismatches } = await api("/transactions/mismatches");
  renderMismatches(mismatches);
}

// Accept Plaid's category for one mismatched transaction (manual override).
window.acceptMismatch = async (id, categoryId) => {
  await api(`/transactions/${encodeURIComponent(id)}/category`, {
    method: "PATCH",
    body: JSON.stringify({ category_id: categoryId }),
  });
  dropMismatchRow(id);
};

// Dismiss a single mismatch without changing the category.
window.ignoreMismatch = async (id) => {
  await api(`/transactions/${encodeURIComponent(id)}/ignore-mismatch`, { method: "POST" });
  dropMismatchRow(id);
};

// Mute every PFC mismatch for a rule (intentional you-vs-Plaid difference).
window.muteRuleMismatch = async (ruleId) => {
  await api(`/rules/${ruleId}/mute-pfc`, { method: "POST", body: JSON.stringify({ muted: true }) });
  await refreshMismatches();
};

// Persist a row's current keyword + category so tweaks survive a reload.
window.saveSuggestion = async (i) => {
  const t = uncatList[i];
  if (!t) return;
  const pattern = $(`#pat-${i}`).value.trim();
  const cid = parseInt($(`#cat-${i}`).value, 10);
  try {
    await api(`/rules/suggestions/${encodeURIComponent(t.id)}`, {
      method: "PUT",
      body: JSON.stringify({ category_id: Number.isInteger(cid) ? cid : null, pattern }),
    });
  } catch (e) {
    /* non-fatal: a failed save just means this tweak won't persist */
  }
};

let ruleCats = [];   // categories for the edit dropdown
let rulesById = {};  // rule lookup for inline editing
// Populate the standalone "Add a rule" form (direction + category dropdowns).
function fillNewRuleForm() {
  const dir = $("#newrule-dir");
  if (dir && !dir.options.length) dir.innerHTML = dirOptions("out");
  const cat = $("#newrule-cat");
  if (cat) {
    cat.innerHTML = ruleCats.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
  }
}

function renderRulesTable(rules) {
  fillNewRuleForm();
  rulesById = {};
  rules.forEach((r) => (rulesById[r.id] = r));
  $("#rules-table").innerHTML = rules.length
    ? `<table><thead><tr><th>Keyword</th><th>Applies to</th><th>Category</th><th></th></tr></thead><tbody>${rules
        .map(
          (r) => `<tr id="rrow-${r.id}"><td><code>${esc(r.pattern)}</code></td>
          <td>${DIR_LABEL[r.direction] || "Any"}</td><td>${esc(r.category)}</td>
          <td><button class="secondary" onclick="startEditRule(${r.id})">Edit</button>
              <button class="secondary" onclick="deleteRule(${r.id})">✕</button></td></tr>`
        )
        .join("")}</tbody></table>`
    : `<p class="muted">No custom rules yet. Tag a transaction above to create one.</p>`;
}

// Turn a rule row into an inline editor (keyword + category).
window.startEditRule = (id) => {
  const r = rulesById[id];
  const row = document.getElementById(`rrow-${id}`);
  if (!r || !row) return;
  const opts = ruleCats
    .map((c) => `<option value="${c.id}" ${c.id === r.category_id ? "selected" : ""}>${esc(c.name)}</option>`)
    .join("");
  row.classList.add("editing");
  row.innerHTML = `
    <td><input id="erp-${id}" value="${esc(r.pattern)}" style="width:140px" /></td>
    <td>${dirSelect(`erd-${id}`, r.direction || "any")}</td>
    <td><select id="erc-${id}">${opts}</select></td>
    <td><button onclick="saveRuleEdit(${id})">Save</button>
        <button class="secondary" onclick="refreshRulesTable()">Cancel</button></td>`;
  row.scrollIntoView({ block: "center" });
  $(`#erp-${id}`)?.focus();
};

window.saveRuleEdit = async (id) => {
  const pattern = $(`#erp-${id}`).value.trim();
  const cid = parseInt($(`#erc-${id}`).value, 10);
  const direction = $(`#erd-${id}`).value;
  if (!pattern) {
    $("#suggest-status").textContent = "Keyword cannot be empty.";
    return;
  }
  let r;
  try {
    r = await api(`/rules/${id}`, { method: "PUT", body: JSON.stringify({ pattern, category_id: cid, direction }) });
  } catch (e) {
    $("#suggest-status").textContent = "⚠️ Couldn't save rule: " + e.message;
    return;
  }
  $("#suggest-status").textContent = `Rule updated — ${r.affected} transaction(s) re-categorized.`;
  await loadRules(); // refresh rules + uncategorized (categories may have shifted)
};

// Remove rows from the uncategorized table WITHOUT re-rendering the whole table
// — re-rendering would wipe the AI suggestions still pending on other rows.
function removeUncatRow(i) {
  const row = document.getElementById(`urow-${i}`);
  if (row) row.remove();
  const left = (parseInt($("#uncat-count").textContent, 10) || 1) - 1;
  $("#uncat-count").textContent = Math.max(0, left);
}

// A new rule re-categorizes EVERY matching transaction on the backend. Rather
// than re-implement the match in JS (which can drift from the server), the
// /rules response tells us exactly which transaction ids it updated — remove
// precisely those rows from the queue, leaving other rows' suggestions intact.
function removeUncatRowsByIds(ids) {
  const idSet = new Set(ids || []);
  let removed = 0;
  uncatList.forEach((t, i) => {
    if (!idSet.has(t.id)) return;
    const row = document.getElementById(`urow-${i}`);
    if (row) {
      row.remove();
      removed++;
    }
  });
  const left = (parseInt($("#uncat-count").textContent, 10) || removed) - removed;
  $("#uncat-count").textContent = Math.max(0, left);
}

async function refreshRulesTable() {
  const { rules } = await api("/rules");
  renderRulesTable(rules);
}
window.applyRule = async (i) => {
  const pattern = $(`#pat-${i}`).value.trim();
  const cid = parseInt($(`#cat-${i}`).value, 10);
  const direction = $(`#dir-${i}`).value;
  if (!pattern) {
    $("#suggest-status").textContent = "Enter a keyword first.";
    return;
  }
  if (!Number.isInteger(cid)) {
    $("#suggest-status").textContent = "Pick a category first.";
    return;
  }
  let r;
  try {
    r = await api("/rules", { method: "POST", body: JSON.stringify({ pattern, category_id: cid, direction }) });
  } catch (e) {
    $("#suggest-status").textContent = "⚠️ Couldn't save rule: " + e.message;
    return;
  }
  $("#suggest-status").textContent = `Rule added — ${r.affected} transaction(s) updated.`;
  // Clear exactly the rows the server re-categorized (category-independent),
  // leaving non-matching rows' AI suggestions untouched.
  removeUncatRowsByIds(r.affected_ids);
  await refreshRulesTable();
};
window.setOnce = async (i) => {
  const t = uncatList[i];
  const cid = parseInt($(`#cat-${i}`).value, 10);
  if (!Number.isInteger(cid)) {
    $("#suggest-status").textContent = "Pick a category first.";
    return;
  }
  try {
    await api(`/transactions/${encodeURIComponent(t.id)}/category`, {
      method: "PATCH",
      body: JSON.stringify({ category_id: cid }),
    });
  } catch (e) {
    $("#suggest-status").textContent = "⚠️ Couldn't set category: " + e.message;
    return;
  }
  $("#suggest-status").textContent = "Category set.";
  removeUncatRow(i);
};
window.deleteRule = async (id) => {
  const r = await api(`/rules/${id}`, { method: "DELETE" });
  $("#suggest-status").textContent = `Rule deleted — ${r.affected} transaction(s) re-categorized.`;
  // Deleting now re-evaluates the rule's transactions against remaining rules,
  // so some may return to Uncategorized — reload the whole view to reflect it.
  await loadRules();
};

// Standalone add-rule form: create a rule with no uncategorized transaction.
$("#newrule-add").addEventListener("click", async () => {
  const pattern = $("#newrule-pattern").value.trim();
  const cid = parseInt($("#newrule-cat").value, 10);
  const direction = $("#newrule-dir").value;
  if (!pattern) {
    $("#newrule-status").textContent = "Enter a keyword first.";
    return;
  }
  if (!Number.isInteger(cid)) {
    $("#newrule-status").textContent = "Pick a category first.";
    return;
  }
  let r;
  try {
    r = await api("/rules", { method: "POST", body: JSON.stringify({ pattern, category_id: cid, direction }) });
  } catch (e) {
    $("#newrule-status").textContent = "⚠️ Couldn't save rule: " + e.message;
    return;
  }
  $("#newrule-status").textContent = `Rule added — ${r.affected} transaction(s) updated.`;
  $("#newrule-pattern").value = "";
  await loadRules();
});

// Add a new category, then refresh so it appears in every category dropdown.
$("#newcat-add").addEventListener("click", async () => {
  const name = $("#newcat-name").value.trim();
  const kind = $("#newcat-kind").value;
  if (!name) {
    $("#newcat-status").textContent = "Enter a category name first.";
    return;
  }
  try {
    await api("/transactions/categories", { method: "POST", body: JSON.stringify({ name, kind }) });
  } catch (e) {
    $("#newcat-status").textContent = "⚠️ " + e.message;
    return;
  }
  $("#newcat-status").textContent = `Added “${name}”.`;
  $("#newcat-name").value = "";
  await loadRules();
});
$("#suggest-cats-btn").addEventListener("click", async () => {
  if (!uncatList.length) return;
  $("#suggest-status").textContent = "Asking the assistant to suggest categories…";
  try {
    const { suggestions } = await api("/rules/suggest", { method: "POST" });
    const byId = {};
    suggestions.forEach((s) => (byId[s.transaction_id] = s));
    uncatList.forEach((t, i) => {
      const s = byId[t.id];
      if (!s) return;
      if (s.category_id) $(`#cat-${i}`).value = s.category_id;
      if (s.pattern) $(`#pat-${i}`).value = s.pattern;
    });
    $("#suggest-status").textContent = `Suggested ${suggestions.length}. Review each, then click Add rule.`;
  } catch (e) {
    $("#suggest-status").textContent = "⚠️ " + e.message;
  }
});

// ---------------------------------------------------------------- assistant
$("#chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#chat-question").value.trim();
  if (!q) return;
  addMsg("user", q);
  $("#chat-question").value = "";
  const thinking = addMsg("bot", "Thinking…");
  try {
    const r = await api("/chat", { method: "POST", body: JSON.stringify({ question: q }) });
    thinking.remove();
    renderBotAnswer(r);
  } catch (err) {
    thinking.textContent = "⚠️ " + err.message;
  }
});
function addMsg(who, text) {
  const div = document.createElement("div");
  div.className = `msg ${who}`;
  div.textContent = text;
  $("#chat-log").appendChild(div);
  div.scrollIntoView({ behavior: "smooth" });
  return div;
}
const esc = (s) => String(s ?? "").replace(/</g, "&lt;");
function renderBotAnswer(r) {
  const div = document.createElement("div");
  div.className = "msg bot";
  let html = "";
  if (r.affordable === true) html += `<div class="verdict yes">✓ Yes, you can afford it</div>`;
  else if (r.affordable === false) html += `<div class="verdict no">✕ Better to hold off</div>`;
  html += `<div>${esc(r.answer)}</div>`;
  if (r.key_points?.length)
    html += `<ul class="points">${r.key_points.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>`;
  div.innerHTML = html;

  // One-click-apply proposals from the assistant.
  if (r.proposed_budgets?.length) {
    const box = document.createElement("div");
    box.className = "proposal";
    box.innerHTML =
      `<div class="ptitle">📊 Suggested budget</div>` +
      `<table><tbody>${r.proposed_budgets
        .map((b) => `<tr><td>${esc(b.category)}</td><td class="num">${money(b.monthly_limit)}</td><td class="muted">${esc(b.rationale)}</td></tr>`)
        .join("")}</tbody></table>`;
    const btn = document.createElement("button");
    btn.textContent = "Apply these budgets";
    btn.onclick = () => applyBudgets(r.proposed_budgets, btn);
    box.appendChild(btn);
    div.appendChild(box);
  }
  if (r.proposed_goals?.length) {
    const box = document.createElement("div");
    box.className = "proposal";
    box.innerHTML =
      `<div class="ptitle">🎯 Suggested goal${r.proposed_goals.length > 1 ? "s" : ""}</div>` +
      `<table><tbody>${r.proposed_goals
        .map((g) => `<tr><td>${esc(g.name)}</td><td class="num">${money(g.target_amount)}</td><td class="muted">${esc(g.target_date || "")} ${esc(g.rationale)}</td></tr>`)
        .join("")}</tbody></table>`;
    const btn = document.createElement("button");
    btn.textContent = r.proposed_goals.length > 1 ? "Add these goals" : "Add this goal";
    btn.onclick = () => applyGoals(r.proposed_goals, btn);
    box.appendChild(btn);
    div.appendChild(box);
  }

  $("#chat-log").appendChild(div);
  div.scrollIntoView({ behavior: "smooth" });
}
async function applyBudgets(list, btn) {
  btn.disabled = true;
  btn.textContent = "Applying…";
  const { categories } = await api("/transactions/categories");
  const idByName = {};
  categories.forEach((c) => (idByName[c.name.toLowerCase()] = c.id));
  let n = 0;
  for (const b of list) {
    const cid = idByName[(b.category || "").toLowerCase()];
    if (cid && b.monthly_limit >= 0) {
      await api(`/budgets/${cid}`, { method: "PUT", body: JSON.stringify({ monthly_limit: b.monthly_limit }) });
      n++;
    }
  }
  btn.textContent = `Applied ${n} budget${n === 1 ? "" : "s"} ✓`;
}
async function applyGoals(list, btn) {
  btn.disabled = true;
  btn.textContent = "Adding…";
  for (const g of list) {
    await api("/goals", {
      method: "POST",
      body: JSON.stringify({
        name: g.name,
        target_amount: g.target_amount,
        current_amount: g.current_amount || 0,
        target_date: g.target_date || null,
        priority: g.priority || 100,
      }),
    });
  }
  btn.textContent = "Added ✓";
}
// Jump to the assistant and ask a preset question.
function gotoAssistantWith(q) {
  $('.nav-item[data-view="assistant"]').click();
  $("#chat-question").value = q;
  $("#chat-form").requestSubmit();
}
$("#ai-budget-btn")?.addEventListener("click", () =>
  gotoAssistantWith("Help me build a realistic monthly budget based on our spending history, leaving room for our goals.")
);
$("#ai-goal-btn")?.addEventListener("click", () =>
  gotoAssistantWith("Help me set a sensible savings goal based on our finances.")
);

// ---------------------------------------------------------------- setup
$("#load-sample-btn").addEventListener("click", async () => {
  $("#setup-result").textContent = "Loading…";
  const r = await api("/dev/load-sample", { method: "POST" });
  $("#setup-result").textContent = `Loaded ${r.accounts} accounts, ${r.transactions} transactions, ${r.goals} goals.`;
  loadDashboard();
});
$("#reset-btn").addEventListener("click", async () => {
  if (!confirm("Delete all local data?")) return;
  await api("/dev/reset", { method: "POST" });
  $("#setup-result").textContent = "All data cleared.";
});
$("#link-connect-btn").addEventListener("click", async () => {
  const out = $("#setup-result");
  out.textContent = "Preparing a secure Plaid connection…";
  let data;
  try {
    data = await api("/plaid/hosted-link", { method: "POST" });
  } catch (e) {
    out.textContent = "⚠️ " + e.message;
    return;
  }
  // Open Plaid's hosted login in a new tab; the bank login (incl. OAuth)
  // happens on Plaid's secure domain. We poll here until it's done.
  window.open(data.hosted_link_url, "_blank", "noopener");
  out.innerHTML =
    'Complete your bank login in the new tab, then return here.<br>' +
    '<span class="muted">Waiting for the connection… (you can close the Plaid tab when it says you\'re done)</span>';

  const startedAt = Date.now();
  const poll = async () => {
    if (Date.now() - startedAt > 5 * 60 * 1000) {
      out.textContent = "Timed out waiting for the connection. Click Connect to try again.";
      return;
    }
    let r;
    try {
      r = await api("/plaid/hosted-link/poll", {
        method: "POST",
        body: JSON.stringify({ link_token: data.link_token }),
      });
    } catch (e) {
      out.textContent = "⚠️ " + e.message;
      return;
    }
    if (r.status === "connected") {
      out.textContent = `Connected ${r.institutions.join(", ")}. Synced ${r.transactions_added} transactions.`;
      loadDashboard();
      return;
    }
    setTimeout(poll, 3000);
  };
  setTimeout(poll, 3000);
});
$("#sandbox-connect-btn").addEventListener("click", async () => {
  $("#setup-result").textContent = "Connecting sandbox bank…";
  try {
    const r = await api("/plaid/sandbox-connect", { method: "POST", body: JSON.stringify({}) });
    $("#setup-result").textContent = `Connected. Synced ${r.transactions_added} transactions.`;
  } catch (e) {
    $("#setup-result").textContent = "⚠️ " + e.message;
  }
});
$("#sync-btn").addEventListener("click", async () => {
  $("#setup-result").textContent = "Syncing…";
  try {
    const r = await api("/plaid/sync", { method: "POST" });
    $("#setup-result").textContent = `Synced ${r.transactions_added} transactions across ${r.items} item(s).`;
  } catch (e) {
    $("#setup-result").textContent = "⚠️ " + e.message;
  }
});

// ---------------------------------------------------------------- boot
// Charts are server-rendered PNGs; bust the cache so each visit re-renders fresh.
function loadCharts() {
  const t = Date.now();
  $("#chart-goals").src = `/api/charts/goals.png?t=${t}`;
  $("#chart-networth").src = `/api/charts/networth.png?t=${t}`;
  $("#chart-cashflow").src = `/api/charts/cashflow.png?t=${t}`;
}

const VIEWS = {
  dashboard: loadDashboard,
  charts: loadCharts,
  accounts: loadAccounts,
  budgets: loadBudgets,
  goals: loadGoals,
  recurring: loadRecurring,
  transactions: loadTransactions,
  rules: loadRules,
  assistant: () => {},
  setup: () => {},
};
loadStatus();
loadDashboard();
