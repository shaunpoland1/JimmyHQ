import { Search } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { filterCommands, stepIndex } from "../lib/palette";
import type { Command } from "../lib/palette";
import { Modal } from "./Modal";

export type PaletteCommand = Command & { run: () => void };

type CommandPaletteProps = { commands: readonly PaletteCommand[]; onClose: () => void };

/** Command palette: type to filter, arrows to move, Enter to run. */
export function CommandPalette({ commands, onClose }: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listId = useId();
  const list = useRef<HTMLUListElement>(null);
  const results = useMemo(() => filterCommands(commands, query), [commands, query]);
  const index = Math.min(active, Math.max(0, results.length - 1));

  useEffect(() => {
    list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }, [index, results]);

  const run = (command: PaletteCommand | undefined) => {
    if (!command) return;
    onClose();
    command.run();
  };

  return (
    <Modal title="Command palette" onClose={onClose} bare placement="top" widthClass="max-w-lg">
      <div className="flex items-center gap-2 border-b border-border px-3">
        <Search size={15} className="flex-none text-text-faint" aria-hidden="true" />
        <input
          data-autofocus
          type="text"
          role="combobox"
          aria-expanded="true"
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={results[index] ? `${listId}-${results[index].id}` : undefined}
          aria-label="Search commands"
          placeholder="Go to a screen or run a command"
          autoComplete="off"
          spellCheck={false}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
          }}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setActive(stepIndex(index, 1, results.length));
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              setActive(stepIndex(index, -1, results.length));
            } else if (event.key === "Home") {
              event.preventDefault();
              setActive(0);
            } else if (event.key === "End") {
              event.preventDefault();
              setActive(Math.max(0, results.length - 1));
            } else if (event.key === "Enter") {
              event.preventDefault();
              run(results[index]);
            }
          }}
          className="h-11 min-w-0 flex-1 bg-transparent text-sm text-text outline-none"
        />
      </div>
      <ul ref={list} id={listId} role="listbox" aria-label="Commands" className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {results.length === 0 ? (
          <li className="px-3 py-6 text-center text-sm text-text-faint">No matching commands.</li>
        ) : (
          results.map((command, position) => (
            <li
              key={command.id}
              id={`${listId}-${command.id}`}
              role="option"
              aria-selected={position === index}
              onMouseMove={() => setActive(position)}
              onClick={() => run(command)}
              className={`flex items-center justify-between gap-3 rounded-lg px-3 py-2 text-sm ${
                position === index ? "bg-surface-2 text-text" : "text-text-muted"
              }`}
            >
              <span className="truncate">{command.title}</span>
              <span className="flex-none text-[11px] text-text-faint">{command.group}</span>
            </li>
          ))
        )}
      </ul>
    </Modal>
  );
}
