// Tiny dependency-free autocomplete used by the member form and kinship
// search. Markup contract:
//
//   <div class="typeahead-wrap">
//     <input type="text" data-typeahead
//            data-fetch-url="/api/members/search"
//            data-id-input="father_id"
//            [data-genealogy-id="..."]   optional, scopes search to one genealogy
//            [data-gender="M"|"F"]       optional, filters by sex
//            placeholder="..." value="...">
//     <input type="hidden" name="father_id" value="...">
//   </div>
//
// On selection: writes the chosen member's id into the hidden input and the
// chosen name into the visible input. Editing the visible name clears the id
// (so saving an unselected manual entry is a no-op rather than persisting a
// stale id).
(function () {
  function debounce(fn, ms) {
    let t;
    return function (...args) {
      clearTimeout(t);
      t = setTimeout(() => fn.apply(this, args), ms);
    };
  }

  function buildResultLine(m) {
    const sex = m.gender === "M" ? "♂" : "♀";
    const yrs = m.birth_year
      ? `${m.birth_year}–${m.death_year || ""}`
      : "?";
    const gen = m.genealogy_name ? ` · ${m.genealogy_name}` : "";
    return `${m.name} ${sex} · ${yrs}${gen} · #${m.id}`;
  }

  function init(input) {
    const wrap = input.closest(".typeahead-wrap");
    if (!wrap) return;
    const fetchUrl = input.dataset.fetchUrl;
    const idInputName = input.dataset.idInput;
    const hidden = wrap.querySelector(`input[type="hidden"][name="${idInputName}"]`);
    const dropdown = document.createElement("ul");
    dropdown.className = "typeahead-results";
    dropdown.hidden = true;
    wrap.appendChild(dropdown);
    let activeIdx = -1;
    let currentResults = [];

    function close() {
      dropdown.hidden = true;
      activeIdx = -1;
      [...dropdown.children].forEach((li) => li.classList.remove("active"));
    }

    function render(results) {
      currentResults = results;
      dropdown.innerHTML = "";
      if (!results.length) {
        const li = document.createElement("li");
        li.className = "muted";
        li.textContent = "无匹配结果";
        dropdown.appendChild(li);
        dropdown.hidden = false;
        return;
      }
      results.forEach((m, i) => {
        const li = document.createElement("li");
        li.textContent = buildResultLine(m);
        li.dataset.idx = i;
        li.addEventListener("mousedown", (e) => {
          e.preventDefault(); // keep input focused
          select(i);
        });
        dropdown.appendChild(li);
      });
      dropdown.hidden = false;
    }

    function select(i) {
      const m = currentResults[i];
      if (!m) return;
      input.value = m.name;
      if (hidden) hidden.value = m.id;
      input.dataset.selectedId = m.id;
      close();
    }

    function highlight(i) {
      [...dropdown.children].forEach((li, idx) =>
        li.classList.toggle("active", idx === i)
      );
    }

    const search = debounce(async () => {
      const q = input.value.trim();
      if (!q) {
        close();
        return;
      }
      const url = new URL(fetchUrl, window.location.origin);
      url.searchParams.set("q", q);
      if (input.dataset.genealogyId) {
        url.searchParams.set("genealogy_id", input.dataset.genealogyId);
      }
      if (input.dataset.gender) {
        url.searchParams.set("gender", input.dataset.gender);
      }
      try {
        const r = await fetch(url, { credentials: "same-origin" });
        if (!r.ok) {
          close();
          return;
        }
        const data = await r.json();
        render(data.results || []);
      } catch (_e) {
        close();
      }
    }, 180);

    input.addEventListener("input", () => {
      // user typed — invalidate prior id selection until a new pick happens
      if (hidden) hidden.value = "";
      input.dataset.selectedId = "";
      search();
    });
    input.addEventListener("keydown", (e) => {
      if (dropdown.hidden) return;
      const items = [...dropdown.querySelectorAll("li:not(.muted)")];
      if (!items.length) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        activeIdx = (activeIdx + 1) % items.length;
        highlight(activeIdx);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        activeIdx = (activeIdx - 1 + items.length) % items.length;
        highlight(activeIdx);
      } else if (e.key === "Enter" && activeIdx >= 0) {
        e.preventDefault();
        select(activeIdx);
      } else if (e.key === "Escape") {
        close();
      }
    });
    input.addEventListener("focus", () => {
      if (input.value.trim()) search();
    });
    document.addEventListener("click", (e) => {
      if (!wrap.contains(e.target)) close();
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("input[data-typeahead]").forEach(init);
  });
})();
