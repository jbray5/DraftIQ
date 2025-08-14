export async function askAiOpinion(payload: any) {
  const res = await fetch("/api/ai/opinion", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!res.ok) throw new Error("AI error");
  return await res.json(); // { opinion, usedTendencies }
}

export async function loadTendencies() {
  const res = await fetch("/api/league-tendencies");
  return await res.json();
}

export async function saveTendencies(t: any) {
  const res = await fetch("/api/league-tendencies", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(t)
  });
  return await res.json();
}
