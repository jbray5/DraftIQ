// components/Header.jsx
export default function Header({
  query, setQuery, searchRef,
  totalPlayers, shownPlayers,
  undo, redo, pastLength, futureLength,
  refresh
}) {
  return (
    <header className="sticky top-0 z-10 border-b border-zinc-800 bg-zinc-950/80 backdrop-blur">
      <div className="container mx-auto max-w-7xl px-4 py-4 flex items-center justify-between gap-2">
        <h1 className="text-lg sm:text-xl font-semibold">Derek Jeter&apos;s Taco Hole Fantasy Draft - 2025</h1>
        <div className="flex items-center gap-3">
          <div className="relative w-64">
            <input
              ref={searchRef}
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder='Search players… (press "/")'
              className="w-full rounded-xl border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm outline-none focus:border-zinc-500"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery('')}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-200"
              >×</button>
            )}
          </div>
          <span className="hidden sm:inline text-xs text-zinc-400">{shownPlayers} / {totalPlayers}</span>
          <button onClick={undo} disabled={!pastLength} className="px-3 py-2 bg-zinc-800 rounded-xl disabled:opacity-50">Undo</button>
          <button onClick={redo} disabled={!futureLength} className="px-3 py-2 bg-zinc-800 rounded-xl disabled:opacity-50">Redo</button>
          <button onClick={refresh} className="rounded-xl px-4 py-2 text-sm font-medium bg-blue-600 hover:bg-blue-500">Refresh Rankings</button>
        </div>
      </div>
    </header>
  );
}
