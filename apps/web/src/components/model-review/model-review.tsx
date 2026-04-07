/**
 * ParseGrid — Phase 7 model review (single_table OR table_graph).
 *
 * Replaces the old flat-JSON-Schema editor. Operates directly on the typed
 * `DatabaseModel` returned by the API. The router picks the editor based on
 * `extraction_type`. Both editors return the edited DatabaseModel through a
 * single `onApprove` callback.
 *
 * No JSON Schema munging — the server validates identifiers and downgrades
 * structurally invalid relationships in `services.ddl.validate_model`.
 */

"use client";

import {useCallback, useMemo, useState} from "react";
import type {
  ColumnDef,
  ColumnType,
  DatabaseModel,
  RelationshipDef,
  TableDef,
} from "@/lib/api-client";

const COLUMN_TYPES: ColumnType[] = [
  "string",
  "integer",
  "float",
  "boolean",
  "date",
];

interface ModelReviewProps {
  proposedModel: DatabaseModel;
  onApprove: (editedModel: DatabaseModel) => void;
  onReject?: () => void;
  isSubmitting?: boolean;
  errorMessage?: string | null;
}

export function ModelReview({
  proposedModel,
  onApprove,
  onReject,
  isSubmitting = false,
  errorMessage,
}: ModelReviewProps) {
  // Local working copy. Edits never mutate the prop directly so the user can
  // bail out by navigating away or rejecting.
  const [model, setModel] = useState<DatabaseModel>(() => deepClone(proposedModel));

  const updateTable = useCallback(
    (idx: number, updates: Partial<TableDef>) => {
      setModel((prev) => ({
        ...prev,
        tables: prev.tables.map((t, i) => (i === idx ? {...t, ...updates} : t)),
      }));
    },
    [],
  );

  const updateColumn = useCallback(
    (tableIdx: number, colIdx: number, updates: Partial<ColumnDef>) => {
      setModel((prev) => ({
        ...prev,
        tables: prev.tables.map((t, i) =>
          i === tableIdx
            ? {
                ...t,
                columns: t.columns.map((c, j) =>
                  j === colIdx ? {...c, ...updates} : c,
                ),
              }
            : t,
        ),
      }));
    },
    [],
  );

  const addColumn = useCallback((tableIdx: number) => {
    setModel((prev) => ({
      ...prev,
      tables: prev.tables.map((t, i) =>
        i === tableIdx
          ? {
              ...t,
              columns: [
                ...t.columns,
                {name: "", type: "string", description: "", is_primary_key: false},
              ],
            }
          : t,
      ),
    }));
  }, []);

  const removeColumn = useCallback((tableIdx: number, colIdx: number) => {
    setModel((prev) => ({
      ...prev,
      tables: prev.tables.map((t, i) =>
        i === tableIdx
          ? {...t, columns: t.columns.filter((_, j) => j !== colIdx)}
          : t,
      ),
    }));
  }, []);

  const addTable = useCallback(() => {
    setModel((prev) => ({
      ...prev,
      tables: [
        ...prev.tables,
        {
          table_name: "",
          description: "",
          columns: [
            {name: "", type: "string", description: "", is_primary_key: false},
          ],
        },
      ],
    }));
  }, []);

  const removeTable = useCallback((idx: number) => {
    setModel((prev) => {
      const removed = prev.tables[idx]?.table_name;
      return {
        ...prev,
        tables: prev.tables.filter((_, i) => i !== idx),
        // Drop relationships referencing the removed table.
        relationships: prev.relationships.filter(
          (r) => r.source_table !== removed && r.references_table !== removed,
        ),
      };
    });
  }, []);

  const updateRelationship = useCallback(
    (idx: number, updates: Partial<RelationshipDef>) => {
      setModel((prev) => ({
        ...prev,
        relationships: prev.relationships.map((r, i) =>
          i === idx ? {...r, ...updates} : r,
        ),
      }));
    },
    [],
  );

  const addRelationship = useCallback(() => {
    setModel((prev) => {
      const first = prev.tables[0]?.table_name ?? "";
      const second = prev.tables[1]?.table_name ?? first;
      return {
        ...prev,
        relationships: [
          ...prev.relationships,
          {
            source_table: first,
            source_column: "",
            references_table: second,
            references_column: "",
            link_basis: "natural_key",
            composite_key_columns: null,
            nullable: true,
            enabled: true,
          },
        ],
      };
    });
  }, []);

  const removeRelationship = useCallback((idx: number) => {
    setModel((prev) => ({
      ...prev,
      relationships: prev.relationships.filter((_, i) => i !== idx),
    }));
  }, []);

  // Client-side sanity checks. Server-side `validate_model` is authoritative;
  // this is just a guard so the user notices obvious mistakes before submit.
  const clientErrors = useMemo(() => validateLocally(model), [model]);
  const canSubmit = clientErrors.length === 0 && model.tables.length > 0;

  const handleApprove = () => {
    if (!canSubmit) return;
    onApprove(model);
  };

  return (
    <div className='space-y-6'>
      <ModelHeader extractionType={model.extraction_type} />

      {model.tables.map((table, idx) => (
        <TableEditor
          key={idx}
          table={table}
          showRemove={model.tables.length > 1}
          onChange={(updates) => updateTable(idx, updates)}
          onColumnChange={(colIdx, updates) => updateColumn(idx, colIdx, updates)}
          onAddColumn={() => addColumn(idx)}
          onRemoveColumn={(colIdx) => removeColumn(idx, colIdx)}
          onRemoveTable={() => removeTable(idx)}
        />
      ))}

      {model.extraction_type === "table_graph" && (
        <>
          <button
            type='button'
            onClick={addTable}
            className='flex w-full items-center justify-center gap-2 rounded-xl border border-dashed border-zinc-800 px-4 py-3 text-sm text-zinc-500 transition-colors hover:border-emerald-600 hover:text-emerald-400'>
            <PlusIcon />
            Add table
          </button>

          <RelationshipList
            relationships={model.relationships}
            tables={model.tables}
            onChange={updateRelationship}
            onAdd={addRelationship}
            onRemove={removeRelationship}
          />
        </>
      )}

      {clientErrors.length > 0 && (
        <div className='rounded-xl border border-amber-500/20 bg-amber-500/5 px-4 py-3 text-sm text-amber-300'>
          <p className='font-medium'>Fix these before approving:</p>
          <ul className='mt-1 list-inside list-disc space-y-0.5 text-xs'>
            {clientErrors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      )}

      {errorMessage && (
        <div className='rounded-xl border border-red-500/20 bg-red-500/5 px-4 py-3 text-sm text-red-300'>
          {errorMessage}
        </div>
      )}

      <div className='flex items-center justify-end gap-3 border-t border-zinc-800/60 pt-5'>
        {onReject && (
          <button
            type='button'
            onClick={onReject}
            disabled={isSubmitting}
            className='rounded-xl border border-zinc-800 px-6 py-2.5 text-sm font-medium text-zinc-400 transition-all hover:border-zinc-700 hover:text-zinc-200 active:scale-[0.98] disabled:opacity-50'>
            Reject & retry
          </button>
        )}
        <button
          type='button'
          onClick={handleApprove}
          disabled={isSubmitting || !canSubmit}
          className='flex items-center gap-2 rounded-xl bg-emerald-600 px-6 py-2.5 text-sm font-medium text-white transition-all hover:bg-emerald-500 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50'>
          {isSubmitting ? (
            <>
              <Spinner />
              Approving…
            </>
          ) : (
            "Approve & extract"
          )}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function ModelHeader({extractionType}: {extractionType: DatabaseModel["extraction_type"]}) {
  return (
    <div className='flex items-center justify-between'>
      <div>
        <h3 className='text-base font-semibold text-zinc-100'>
          Review extraction model
        </h3>
        <p className='mt-1 text-sm text-zinc-500'>
          {extractionType === "table_graph"
            ? "The AI proposed a relational model. Edit tables, columns, and relationships before extraction."
            : "The AI proposed a single table. Edit columns and types before extraction."}
        </p>
      </div>
      <span className='rounded-lg bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-amber-400'>
        {extractionType === "table_graph" ? "Table graph" : "Single table"}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-table editor (used for both single_table and each table in a graph)
// ---------------------------------------------------------------------------

interface TableEditorProps {
  table: TableDef;
  showRemove: boolean;
  onChange: (updates: Partial<TableDef>) => void;
  onColumnChange: (colIdx: number, updates: Partial<ColumnDef>) => void;
  onAddColumn: () => void;
  onRemoveColumn: (colIdx: number) => void;
  onRemoveTable: () => void;
}

function TableEditor({
  table,
  showRemove,
  onChange,
  onColumnChange,
  onAddColumn,
  onRemoveColumn,
  onRemoveTable,
}: TableEditorProps) {
  return (
    <div className='space-y-3 rounded-xl border border-zinc-800/60 bg-zinc-950/40 p-4'>
      <div className='flex items-center gap-3'>
        <input
          type='text'
          value={table.table_name}
          onChange={(e) => onChange({table_name: e.target.value})}
          className='flex-1 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 font-mono text-sm text-zinc-100 transition-colors focus:border-emerald-600 focus:outline-none'
          placeholder='table_name'
        />
        {showRemove && (
          <button
            type='button'
            onClick={onRemoveTable}
            className='rounded-lg p-1.5 text-zinc-600 transition-colors hover:bg-red-500/10 hover:text-red-400'
            aria-label='Remove table'>
            <CloseIcon />
          </button>
        )}
      </div>
      <input
        type='text'
        value={table.description}
        onChange={(e) => onChange({description: e.target.value})}
        className='w-full rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-sm text-zinc-300 transition-colors focus:border-emerald-600 focus:outline-none'
        placeholder='Short description of what this table holds'
      />

      <div className='overflow-hidden rounded-lg border border-zinc-800/60'>
        <table className='w-full'>
          <thead>
            <tr className='border-b border-zinc-800/60 text-left text-xs font-medium uppercase tracking-wider text-zinc-500'>
              <th className='px-3 py-2'>Column</th>
              <th className='px-3 py-2 w-32'>Type</th>
              <th className='px-3 py-2'>Description</th>
              <th className='px-3 py-2 w-16 text-center'>Key</th>
              <th className='px-3 py-2 w-8' />
            </tr>
          </thead>
          <tbody className='divide-y divide-zinc-800/40'>
            {table.columns.map((col, colIdx) => (
              <tr key={colIdx} className='transition-colors hover:bg-zinc-800/20'>
                <td className='px-3 py-1.5'>
                  <input
                    type='text'
                    value={col.name}
                    onChange={(e) => onColumnChange(colIdx, {name: e.target.value})}
                    className='w-full rounded-md border border-zinc-800 bg-zinc-900/60 px-2 py-1 font-mono text-sm text-zinc-200 transition-colors focus:border-emerald-600 focus:outline-none'
                    placeholder='column_name'
                  />
                </td>
                <td className='px-3 py-1.5'>
                  <select
                    value={col.type}
                    onChange={(e) =>
                      onColumnChange(colIdx, {type: e.target.value as ColumnType})
                    }
                    className='w-full rounded-md border border-zinc-800 bg-zinc-900/60 px-2 py-1 text-sm text-zinc-200 transition-colors focus:border-emerald-600 focus:outline-none'>
                    {COLUMN_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </td>
                <td className='px-3 py-1.5'>
                  <input
                    type='text'
                    value={col.description}
                    onChange={(e) =>
                      onColumnChange(colIdx, {description: e.target.value})
                    }
                    className='w-full rounded-md border border-zinc-800 bg-zinc-900/60 px-2 py-1 text-sm text-zinc-300 transition-colors focus:border-emerald-600 focus:outline-none'
                    placeholder='Field description'
                  />
                </td>
                <td className='px-3 py-1.5 text-center'>
                  <button
                    type='button'
                    onClick={() =>
                      onColumnChange(colIdx, {is_primary_key: !col.is_primary_key})
                    }
                    title='Mark as natural key (target of foreign keys)'
                    className={`inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                      col.is_primary_key ? "bg-emerald-600" : "bg-zinc-700"
                    }`}>
                    <span
                      className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
                        col.is_primary_key ? "translate-x-[18px]" : "translate-x-[3px]"
                      }`}
                    />
                  </button>
                </td>
                <td className='px-3 py-1.5'>
                  <button
                    type='button'
                    onClick={() => onRemoveColumn(colIdx)}
                    className='rounded-md p-1 text-zinc-600 transition-colors hover:bg-red-500/10 hover:text-red-400'
                    aria-label='Remove column'>
                    <CloseIcon />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <button
        type='button'
        onClick={onAddColumn}
        className='flex items-center gap-2 rounded-lg border border-dashed border-zinc-800 px-3 py-1.5 text-xs text-zinc-500 transition-colors hover:border-emerald-600 hover:text-emerald-400'>
        <PlusIcon />
        Add column
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Relationship list (table_graph only)
// ---------------------------------------------------------------------------

interface RelationshipListProps {
  relationships: RelationshipDef[];
  tables: TableDef[];
  onChange: (idx: number, updates: Partial<RelationshipDef>) => void;
  onAdd: () => void;
  onRemove: (idx: number) => void;
}

function RelationshipList({
  relationships,
  tables,
  onChange,
  onAdd,
  onRemove,
}: RelationshipListProps) {
  const tableNames = tables.map((t) => t.table_name).filter(Boolean);
  const columnsByTable: Record<string, string[]> = Object.fromEntries(
    tables.map((t) => [t.table_name, t.columns.map((c) => c.name).filter(Boolean)]),
  );

  return (
    <div className='space-y-3 rounded-xl border border-zinc-800/60 bg-zinc-950/40 p-4'>
      <div className='flex items-center justify-between'>
        <h4 className='text-xs font-semibold uppercase tracking-wider text-zinc-500'>
          Relationships
        </h4>
        <span className='text-xs text-zinc-600'>
          References must point to a column marked as a key
        </span>
      </div>

      {relationships.length === 0 && (
        <p className='py-2 text-xs text-zinc-600'>
          No relationships proposed.
        </p>
      )}

      {relationships.map((rel, idx) => {
        const sourceColumns = columnsByTable[rel.source_table] ?? [];
        const refColumns = columnsByTable[rel.references_table] ?? [];
        return (
          <div
            key={idx}
            className={`grid grid-cols-[1fr_1fr_auto_1fr_1fr_auto_auto] items-center gap-2 rounded-lg border px-3 py-2 ${
              rel.enabled
                ? "border-zinc-800/60 bg-zinc-900/40"
                : "border-zinc-800/40 bg-zinc-900/10 opacity-60"
            }`}>
            <select
              value={rel.source_table}
              onChange={(e) => onChange(idx, {source_table: e.target.value})}
              className='rounded-md border border-zinc-800 bg-zinc-900/60 px-2 py-1 font-mono text-xs text-zinc-200 transition-colors focus:border-emerald-600 focus:outline-none'>
              {tableNames.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <select
              value={rel.source_column}
              onChange={(e) => onChange(idx, {source_column: e.target.value})}
              className='rounded-md border border-zinc-800 bg-zinc-900/60 px-2 py-1 font-mono text-xs text-zinc-200 transition-colors focus:border-emerald-600 focus:outline-none'>
              <option value=''>—</option>
              {sourceColumns.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            <span className='px-1 text-xs text-zinc-600'>→</span>
            <select
              value={rel.references_table}
              onChange={(e) => onChange(idx, {references_table: e.target.value})}
              className='rounded-md border border-zinc-800 bg-zinc-900/60 px-2 py-1 font-mono text-xs text-zinc-200 transition-colors focus:border-emerald-600 focus:outline-none'>
              {tableNames.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <select
              value={rel.references_column}
              onChange={(e) => onChange(idx, {references_column: e.target.value})}
              className='rounded-md border border-zinc-800 bg-zinc-900/60 px-2 py-1 font-mono text-xs text-zinc-200 transition-colors focus:border-emerald-600 focus:outline-none'>
              <option value=''>—</option>
              {refColumns.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            <button
              type='button'
              onClick={() => onChange(idx, {enabled: !rel.enabled})}
              title={rel.enabled ? "Disable relationship" : "Enable relationship"}
              className={`inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                rel.enabled ? "bg-emerald-600" : "bg-zinc-700"
              }`}>
              <span
                className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
                  rel.enabled ? "translate-x-[18px]" : "translate-x-[3px]"
                }`}
              />
            </button>
            <button
              type='button'
              onClick={() => onRemove(idx)}
              className='rounded-md p-1 text-zinc-600 transition-colors hover:bg-red-500/10 hover:text-red-400'
              aria-label='Remove relationship'>
              <CloseIcon />
            </button>
          </div>
        );
      })}

      {tableNames.length >= 2 && (
        <button
          type='button'
          onClick={onAdd}
          className='flex items-center gap-2 rounded-lg border border-dashed border-zinc-800 px-3 py-1.5 text-xs text-zinc-500 transition-colors hover:border-emerald-600 hover:text-emerald-400'>
          <PlusIcon />
          Add relationship
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function deepClone<T>(value: T): T {
  if (typeof structuredClone === "function") {
    return structuredClone(value);
  }
  return JSON.parse(JSON.stringify(value));
}

function validateLocally(model: DatabaseModel): string[] {
  const errors: string[] = [];
  const seenTables = new Set<string>();
  for (const table of model.tables) {
    if (!table.table_name.trim()) {
      errors.push("Every table needs a name.");
      continue;
    }
    if (seenTables.has(table.table_name)) {
      errors.push(`Duplicate table name: ${table.table_name}`);
    }
    seenTables.add(table.table_name);
    if (table.columns.length === 0) {
      errors.push(`Table ${table.table_name} has no columns.`);
    }
    const seenCols = new Set<string>();
    for (const col of table.columns) {
      if (!col.name.trim()) {
        errors.push(`Empty column on ${table.table_name}.`);
        continue;
      }
      if (seenCols.has(col.name)) {
        errors.push(`Duplicate column ${col.name} on ${table.table_name}.`);
      }
      seenCols.add(col.name);
    }
  }
  return errors;
}

// ---------------------------------------------------------------------------
// Inline icons (no Phosphor dependency for these tiny ones)
// ---------------------------------------------------------------------------

function CloseIcon() {
  return (
    <svg className='h-3.5 w-3.5' fill='none' viewBox='0 0 24 24' stroke='currentColor' strokeWidth={2}>
      <path strokeLinecap='round' strokeLinejoin='round' d='M6 18L18 6M6 6l12 12' />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg className='h-3.5 w-3.5' fill='none' viewBox='0 0 24 24' stroke='currentColor' strokeWidth={2}>
      <path strokeLinecap='round' strokeLinejoin='round' d='M12 4v16m8-8H4' />
    </svg>
  );
}

function Spinner() {
  return (
    <svg className='h-4 w-4 animate-spin' viewBox='0 0 24 24'>
      <circle className='opacity-25' cx='12' cy='12' r='10' stroke='currentColor' strokeWidth='4' fill='none' />
      <path className='opacity-75' fill='currentColor' d='M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z' />
    </svg>
  );
}
