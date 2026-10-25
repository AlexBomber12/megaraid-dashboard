(function () {
  // Remembers the active sort per table id so live refreshes keep it.
  const activeSort = {};

  function cellValue(row, index) {
    const cell = row.cells[index];
    if (!cell) return "";
    return cell.dataset.sortValue || cell.textContent.trim();
  }

  function compareValues(left, right) {
    const numeric = /^-?\d+(?:\.\d+)?$/;
    if (numeric.test(left) && numeric.test(right)) return Number(left) - Number(right);
    return left.localeCompare(right, undefined, { numeric: true, sensitivity: "base" });
  }

  function applySort(table, header, direction) {
    const tbody = table.tBodies[0];
    if (!tbody) return;
    const index = Array.prototype.indexOf.call(header.parentElement.children, header);
    const rows = Array.prototype.slice.call(tbody.rows);
    rows.sort(function (a, b) {
      const result = compareValues(cellValue(a, index), cellValue(b, index));
      return direction === "asc" ? result : -result;
    });
    table.querySelectorAll("th[data-sort-key]").forEach(function (th) {
      th.removeAttribute("aria-sort");
      delete th.dataset.sortDirection;
    });
    header.dataset.sortDirection = direction;
    header.setAttribute("aria-sort", direction === "asc" ? "ascending" : "descending");
    rows.forEach(function (row) { tbody.appendChild(row); });
    if (table.id) activeSort[table.id] = { key: header.dataset.sortKey, direction: direction };
  }

  function bindTables(root) {
    (root || document).querySelectorAll("table.data-table").forEach(function (table) {
      table.querySelectorAll("th[data-sort-key]").forEach(function (header) {
        if (header.dataset.sortBound === "true") return;
        header.dataset.sortBound = "true";
        const trigger = header.querySelector(".sort-button") || header;
        trigger.addEventListener("click", function () {
          const next = header.dataset.sortDirection === "asc" ? "desc" : "asc";
          applySort(table, header, next);
        });
      });
      const saved = table.id && activeSort[table.id];
      if (saved) {
        const header = table.querySelector('th[data-sort-key="' + saved.key + '"]');
        if (header) applySort(table, header, saved.direction);
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () { bindTables(document); });
  document.addEventListener("htmx:afterSwap", function (e) { bindTables(e.target); });
  document.addEventListener("live:updated", function (e) { bindTables(e.target); });
})();
