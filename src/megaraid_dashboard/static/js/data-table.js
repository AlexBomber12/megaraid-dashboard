(function () {
  function cellValue(row, index) {
    var cell = row.cells[index];
    if (!cell) {
      return "";
    }
    return cell.dataset.sortValue || cell.textContent.trim();
  }

  function compareValues(left, right) {
    var leftNumber = parseFloat(left);
    var rightNumber = parseFloat(right);
    if (!Number.isNaN(leftNumber) && !Number.isNaN(rightNumber)) {
      return leftNumber - rightNumber;
    }
    return left.localeCompare(right, undefined, { numeric: true, sensitivity: "base" });
  }

  function sortTable(table, header) {
    var tbody = table.tBodies[0];
    if (!tbody) {
      return;
    }
    var columnIndex = Array.prototype.indexOf.call(header.parentElement.children, header);
    var nextDirection = header.dataset.sortDirection === "asc" ? "desc" : "asc";
    var rows = Array.prototype.slice.call(tbody.rows);

    rows.sort(function (leftRow, rightRow) {
      var result = compareValues(
        cellValue(leftRow, columnIndex),
        cellValue(rightRow, columnIndex),
      );
      return nextDirection === "asc" ? result : -result;
    });

    table.querySelectorAll("th[data-sort-key]").forEach(function (sortableHeader) {
      delete sortableHeader.dataset.sortDirection;
    });
    header.dataset.sortDirection = nextDirection;
    rows.forEach(function (row) {
      tbody.appendChild(row);
    });
  }

  function bindTables() {
    document.querySelectorAll("table.data-table").forEach(function (table) {
      table.querySelectorAll("th[data-sort-key]").forEach(function (header) {
        if (header.dataset.sortBound === "true") {
          return;
        }
        header.dataset.sortBound = "true";
        header.addEventListener("click", function () {
          sortTable(table, header);
        });
      });
    });
  }

  document.addEventListener("DOMContentLoaded", bindTables);
  document.addEventListener("htmx:afterSwap", bindTables);
})();
