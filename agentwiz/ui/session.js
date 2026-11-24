window.agentWizSession = async () => {
  const params = new URLSearchParams(location.hash.slice(1));
  const bootstrap = params.get("bootstrap");
  if (!bootstrap) return sessionStorage.getItem("agentwiz-session");
  history.replaceState(null, "", location.pathname);
  const response = await fetch("/api/session/bootstrap", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({bootstrap})
  });
  if (!response.ok) return null;
  const data = await response.json();
  sessionStorage.setItem("agentwiz-session", data.session);
  return data.session;
};
