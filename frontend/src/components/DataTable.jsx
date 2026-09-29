import { useMemo, useState } from 'react';

/**
 * Compact sortable table.
 * columns: [{key, label, sort?: row => value, render: row => node, className?, title?}]
 */
export default function DataTable({
  columns,
  rows,
  rowKey,
  selectedKey,
  onRowClick,
  initialSort = null,
  caption,
  emptyText = 'No rows.',
  maxHeight,
}) {
  const [sort, setSort] = useState(initialSort);

  const sorted = useMemo(() => {
    const list = Array.isArray(rows) ? [...rows] : [];
    const column = sort && columns.find((c) => c.key === sort.key);
    if (!column?.sort) return list;
    const dir = sort.dir === 'asc' ? 1 : -1;
    return list.sort((a, b) => {
      const va = column.sort(a);
      const vb = column.sort(b);
      const aMissing = va === null || va === undefined || Number.isNaN(va);
      const bMissing = vb === null || vb === undefined || Number.isNaN(vb);
      if (aMissing || bMissing) return aMissing === bMissing ? 0 : aMissing ? 1 : -1;
      if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir;
      return String(va).localeCompare(String(vb)) * dir;
    });
  }, [rows, sort, columns]);

  const toggleSort = (column) => {
    if (!column.sort) return;
    setSort((current) => {
      if (current?.key !== column.key) return { key: column.key, dir: column.defaultDir || 'desc' };
      return { key: column.key, dir: current.dir === 'desc' ? 'asc' : 'desc' };
    });
  };

  const activate = (row) => onRowClick?.(row);

  return (
    <div className="table-wrap" style={maxHeight ? { maxHeight } : undefined}>
      <table className={`data-table${onRowClick ? ' clickable' : ''}`}>
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr>
            {columns.map((column) => {
              const active = sort?.key === column.key;
              return (
                <th
                  key={column.key}
                  scope="col"
                  className={column.className}
                  title={column.title}
                  aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined}
                >
                  {column.sort ? (
                    <button type="button" className="sort-button" onClick={() => toggleSort(column)}>
                      {column.label}
                      <span className="sort-indicator" aria-hidden="true">
                        {active ? (sort.dir === 'asc' ? '▲' : '▼') : '↕'}
                      </span>
                    </button>
                  ) : (
                    column.label
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="empty-cell">{emptyText}</td>
            </tr>
          )}
          {sorted.map((row, index) => {
            const key = rowKey(row, index);
            const selected = selectedKey !== undefined && selectedKey !== null && key === selectedKey;
            return (
              <tr
                key={key}
                className={selected ? 'selected' : undefined}
                aria-selected={onRowClick ? selected : undefined}
                tabIndex={onRowClick ? 0 : undefined}
                onClick={onRowClick ? () => activate(row) : undefined}
                onKeyDown={onRowClick ? (event) => {
                  if (event.target !== event.currentTarget) return;
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    activate(row);
                  }
                } : undefined}
              >
                {columns.map((column) => (
                  <td key={column.key} className={column.className}>{column.render(row, index)}</td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
