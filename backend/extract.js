() => {
  const lines = document.body.innerText.split("\n").map(l => l.trim()).filter(l => l);

  // Calendar: walk from "更多日期" line, take next 7 lines matching date + price.
  const cal = [];
  let inCal = false;
  let calCount = 0;
  for (let i = 0; i < lines.length && calCount < 7; i++) {
    if (lines[i] === "更多日期") { inCal = true; continue; }
    if (!inCal) continue;
    if (/^\d{2}-\d{2}/.test(lines[i])) {
      const price = lines[i + 1] || "";
      if (price.startsWith("¥")) {
        cal.push({ date: lines[i], price });
        calCount++;
      }
    }
  }
  const seen = new Set();
  const calendar = cal.filter(c => {
    if (seen.has(c.date)) return false;
    seen.add(c.date);
    return true;
  });

  // Flight list: walk up from each "订票" button. Pick the card with exactly
  // ONE flight number, a price present, and ≥2 time strings. Mark shared
  // flights (Ctrip shows "共享" in the card text) so the front-end / DB
  // can filter them — only main flights should be persisted per
  // PROJECT_BRIEF §5.1.
  const flights = [];
  const seenCards = new Set();
  document.querySelectorAll("button").forEach(b => {
    if (!(b.innerText || "").trim().includes("订票")) return;
    let card = b;
    for (let k = 0; k < 25 && card.parentElement; k++) {
      card = card.parentElement;
      if (seenCards.has(card)) continue;
      const txt = card.innerText;
      const flightNos = [...txt.matchAll(/[A-Z]{2}\d{3,4}/g)].map(m => m[0]);
      const times = [...txt.matchAll(/\d{2}:\d{2}/g)].map(m => m[0]);
      const hasPrice = /¥\s*\d+/.test(txt);
      if (flightNos.length === 1 && hasPrice && times.length >= 2 && txt.length < 1200) {
        const flightNo = flightNos[0];
        if (flights.find(f => f.flight_no === flightNo)) break;
        const airlineM = txt.match(/(东方|南方|吉祥|春秋|海南|国航|东航|南航|海航|深圳|山东|厦门|四川|天津|上海|重庆|华夏|西部|祥鹏|联合|河北|福州|青岛|江西|桂林|多彩|北部湾|奥凯|九元|东海|幸福|金鹏|瑞丽|红土)航空/);
        const discountM = txt.match(/经济舱(\d+(?:\.\d+)?)折/);
        // PRICE EXTRACTION — a single card may contain several "¥NNN" tokens:
        //   ¥550  (real price, "起")
        //   ¥30   (立减 / 抵 / 送 / 优惠券 — promotional, NOT the fare)
        //   ¥85   (85 折优惠券 — also promotional)
        // The real fare is always the largest. Take max across all ¥-tokens.
        const priceTokens = [...txt.matchAll(/¥\s*(\d+)/g)].map(m => parseInt(m[1], 10));
        const price = priceTokens.length ? Math.max(...priceTokens) : 0;
        const aircraftM = txt.match(/(空客\d{3}|波音\d{3}|A\d{3}|B\d{3}|C919|ARJ\d{2})/);
        const stopsM = txt.match(/经停/);
        // Ctrip marks codeshare flights with the "共享" tag in the card text.
        // "实际承运：" indicates the operating carrier (we keep it for context).
        const isShared = /共享/.test(txt);
        const actualOperatorM = txt.match(/实际承运[:：]\s*([^\s\n]+)/);
        flights.push({
          flight_no: flightNo,
          airline: airlineM ? airlineM[0] : "",
          aircraft: aircraftM ? aircraftM[0] : "",
          price: price,
          dep_time: times[0] || "",
          arr_time: times[1] || "",
          discount: discountM ? discountM[1] + "折" : "",
          has_stops: !!stopsM,
          is_shared: isShared,
          actual_operator: actualOperatorM ? actualOperatorM[1] : ""
        });
        seenCards.add(card);
        break;
      }
    }
  });

  // De-duplicate codeshares: same (dep_time, arr_time, aircraft) is one
  // physical flight. Keep the first main flight encountered, drop the rest
  // (these are codeshares for the same plane).
  const dedup = new Map();
  flights.forEach(f => {
    const key = `${f.dep_time}|${f.arr_time}|${f.aircraft}`;
    if (!dedup.has(key)) {
      dedup.set(key, f);
    } else {
      // If existing entry is shared but this one is main, swap in the main
      const existing = dedup.get(key);
      if (existing.is_shared && !f.is_shared) {
        dedup.set(key, f);
      }
    }
  });
  const dedupedFlights = Array.from(dedup.values());

  // 5-bucket grouping: whole-hour → later bucket rule (per spec).
  const timeBucket = (t) => {
    if (!t) return "";
    const [h, m] = t.split(":").map(Number);
    const minutes = h * 60 + m;
    if (minutes < 6 * 60) return "0-6";
    if (minutes < 9 * 60) return "6-9";
    if (minutes < 15 * 60) return "9-15";
    if (minutes < 21 * 60) return "15-21";
    return "21-24";
  };
  const adjustBucket = (t, bucket) => {
    const m = t.match(/^(\d{2}):00$/);
    if (!m) return bucket;
    const h = parseInt(m[1]);
    if (h === 6) return "6-9";
    if (h === 9) return "9-15";
    if (h === 15) return "15-21";
    if (h === 21) return "21-24";
    return bucket;
  };
  flights.forEach(f => {
    let b = timeBucket(f.dep_time);
    b = adjustBucket(f.dep_time, b);
    f.time_bucket = b;
  });

  return { calendar_count: calendar.length, calendar, flight_count: dedupedFlights.length, flights: dedupedFlights };
};
