// Run by tests/test_live.py with Deno: the chess stepper of the Live page (web/js/live_util.js) against placements made by python-chess.
// usage: deno run --allow-read tests/live_core_check.js <web/js/live_util.js> <cases.json>
globalThis.window = globalThis;
const [utilPath, casesPath] = Deno.args;
(0, eval)(Deno.readTextFileSync(utilPath));
const C = globalThis.A.chess;
const cases = JSON.parse(Deno.readTextFileSync(casesPath));
let bad = 0, n = 0;
for (const c of cases) {
  const g = { start_fen: c.start_fen, uci: [] };
  C.ensure(g);
  g.uci = c.uci;                                   // all moves at once
  C.ensure(g);
  for (let i = 0; i < c.pl.length; i++) { n++; if (g._pl[i] !== c.pl[i]) { bad++; console.log('mismatch at ply', i, g._pl[i], c.pl[i]); break; } }
  const h = { start_fen: c.start_fen, uci: [] };   // and one move at a time, as the stream delivers them
  C.ensure(h);
  for (const u of c.uci) { h.uci.push(u); C.ensure(h); }
  if (JSON.stringify(h._pl) !== JSON.stringify(g._pl)) { bad++; console.log('incremental differs'); }
}
console.log(bad ? 'FAILED ' + bad : 'OK ' + n + ' positions');
Deno.exit(bad ? 1 : 0);
