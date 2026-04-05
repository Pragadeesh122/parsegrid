/**
 * ParseGrid — Schema editor form for human-in-the-loop validation.
 * Emerald accent, clean table styling.
 */

"use client";

import { useCallback, useState } from "react";

interface SchemaField {
  name: string;
  type: string;
  description: string;
  required: boolean;
}

interface SchemaFormProps {
  proposedSchema: Record<string, unknown>;
  onApprove: (editedSchema: Record<string, unknown>) => void;
  onReject?: () => void;
  isSubmitting?: boolean;
}

const FIELD_TYPES = ["string", "integer", "number", "boolean", "date"];

function extractFields(schema: Record<string, unknown>): SchemaField[] {
  const properties =
    (schema as Record<string, Record<string, unknown>>)?.properties || {};
  let targetProps: Record<string, Record<string, unknown>> = {};
  const requiredArr: string[] = (schema as Record<string, string[]>)?.required || [];

  for (const [, propDef] of Object.entries(properties)) {
    const def = propDef as Record<string, unknown>;
    if (def.type === "array" && def.items) {
      const items = def.items as Record<string, unknown>;
      targetProps = (items.properties || {}) as Record<string, Record<string, unknown>>;
      break;
    }
  }

  if (Object.keys(targetProps).length === 0) {
    targetProps = properties as Record<string, Record<string, unknown>>;
  }

  return Object.entries(targetProps).map(([name, def]) => {
    const rawType = (def.type as string) || "string";
    const type = FIELD_TYPES.includes(rawType) ? rawType : "string";
    return {
      name,
      type,
      description: (def.description as string) || "",
      required: requiredArr.includes(name),
    };
  });
}

function rebuildSchema(
  original: Record<string, unknown>,
  fields: SchemaField[],
): Record<string, unknown> {
  const properties: Record<string, Record<string, string>> = {};
  const required: string[] = [];

  for (const field of fields) {
    properties[field.name] = {
      type: field.type,
      description: field.description,
    };
    if (field.required) {
      required.push(field.name);
    }
  }

  const itemSchema = { type: "object", properties, required };

  return {
    ...original,
    title: (original.title as string) || "Extraction Schema",
    type: "object",
    properties: {
      items: {
        type: "array",
        description: "Array of extracted records",
        items: itemSchema,
      },
    },
    required: ["items"],
  };
}

export function SchemaForm({
  proposedSchema,
  onApprove,
  onReject,
  isSubmitting = false,
}: SchemaFormProps) {
  const [fields, setFields] = useState<SchemaField[]>(() =>
    extractFields(proposedSchema),
  );

  const updateField = useCallback(
    (index: number, updates: Partial<SchemaField>) => {
      setFields((prev) =>
        prev.map((f, i) => (i === index ? { ...f, ...updates } : f)),
      );
    },
    [],
  );

  const addField = useCallback(() => {
    setFields((prev) => [
      ...prev,
      { name: "", type: "string", description: "", required: false },
    ]);
  }, []);

  const removeField = useCallback((index: number) => {
    setFields((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleApprove = () => {
    const schema = rebuildSchema(proposedSchema, fields);
    onApprove(schema);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-zinc-100">
          Review Extracted Schema
        </h3>
        <span className="rounded-lg bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-amber-400">
          Awaiting Review
        </span>
      </div>

      <p className="text-sm text-zinc-500">
        The AI has proposed the following schema. Review the fields, adjust types
        and requirements, then approve to begin extraction.
      </p>

      <div className="overflow-hidden rounded-xl border border-zinc-800/60">
        <table className="w-full">
          <thead>
            <tr className="border-b border-zinc-800/60 text-left text-xs font-medium uppercase tracking-wider text-zinc-500">
              <th className="px-4 py-3">Field Name</th>
              <th className="px-4 py-3">Type</th>
              <th className="px-4 py-3">Description</th>
              <th className="px-4 py-3 text-center">Required</th>
              <th className="px-4 py-3 w-10" />
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/40">
            {fields.map((field, index) => (
              <tr
                key={index}
                className="transition-colors hover:bg-zinc-800/20"
              >
                <td className="px-4 py-2">
                  <input
                    type="text"
                    value={field.name}
                    onChange={(e) =>
                      updateField(index, { name: e.target.value })
                    }
                    className="w-full rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-sm text-zinc-200 transition-colors focus:border-emerald-600 focus:outline-none"
                    placeholder="field_name"
                  />
                </td>
                <td className="px-4 py-2">
                  <select
                    value={field.type}
                    onChange={(e) =>
                      updateField(index, { type: e.target.value })
                    }
                    className="w-full rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-sm text-zinc-200 transition-colors focus:border-emerald-600 focus:outline-none"
                  >
                    {FIELD_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="px-4 py-2">
                  <input
                    type="text"
                    value={field.description}
                    onChange={(e) =>
                      updateField(index, { description: e.target.value })
                    }
                    className="w-full rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-sm text-zinc-300 transition-colors focus:border-emerald-600 focus:outline-none"
                    placeholder="Field description..."
                  />
                </td>
                <td className="px-4 py-2 text-center">
                  <button
                    type="button"
                    onClick={() =>
                      updateField(index, { required: !field.required })
                    }
                    className={`inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                      field.required ? "bg-emerald-600" : "bg-zinc-700"
                    }`}
                  >
                    <span
                      className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
                        field.required ? "translate-x-[18px]" : "translate-x-[3px]"
                      }`}
                    />
                  </button>
                </td>
                <td className="px-4 py-2">
                  <button
                    type="button"
                    onClick={() => removeField(index)}
                    className="rounded-lg p-1 text-zinc-600 transition-colors hover:bg-red-500/10 hover:text-red-400"
                  >
                    <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <button
        type="button"
        onClick={addField}
        className="flex items-center gap-2 rounded-lg border border-dashed border-zinc-800 px-4 py-2 text-sm text-zinc-500 transition-colors hover:border-emerald-600 hover:text-emerald-400"
      >
        <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
        </svg>
        Add Field
      </button>

      <div className="flex items-center justify-end gap-3 border-t border-zinc-800/60 pt-5">
        {onReject && (
          <button
            type="button"
            onClick={onReject}
            disabled={isSubmitting}
            className="rounded-xl border border-zinc-800 px-6 py-2.5 text-sm font-medium text-zinc-400 transition-all hover:border-zinc-700 hover:text-zinc-200 active:scale-[0.98] disabled:opacity-50"
          >
            Reject
          </button>
        )}
        <button
          type="button"
          onClick={handleApprove}
          disabled={isSubmitting || fields.length === 0}
          className="flex items-center gap-2 rounded-xl bg-emerald-600 px-6 py-2.5 text-sm font-medium text-white transition-all hover:bg-emerald-500 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isSubmitting ? (
            <>
              <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
              Approving...
            </>
          ) : (
            "Approve & Extract"
          )}
        </button>
      </div>
    </div>
  );
}
