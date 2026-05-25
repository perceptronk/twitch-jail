const channel = window.location.pathname.replace(/^\//, "").toLowerCase();
const timeoutList = document.getElementById("timeoutList");
const deathRowList = document.getElementById("deathRowList");
const executionStage = document.getElementById("executionStage");

const timeouts = new Map();
const deathRow = new Map();
const timeoutCards = new Map();
const deathRowCards = new Map();
const timeoutWalkers = new Map();
const deathRowWalkers = new Map();

const LANE_PADDING = 10;
const CARD_GAP = 10;

let lastMotionTick = performance.now();

function render() {
  const now = Date.now() / 1000;
  scrubLegacyPlaceholders(timeoutList);
  scrubLegacyPlaceholders(deathRowList);
  syncTimeoutCards(now);
  syncDeathRowCards();
}

function scrubLegacyPlaceholders(container) {
  const notes = container.querySelectorAll(".empty-note");
  for (const note of notes) {
    note.remove();
  }

  for (const node of Array.from(container.childNodes)) {
    if (node.nodeType === Node.TEXT_NODE && node.textContent && node.textContent.trim()) {
      node.remove();
    }
  }
}

function syncTimeoutCards(now) {
  const activeUsers = new Set();
  const expired = [];

  for (const [username, entry] of timeouts) {
    const remaining = Math.max(0, Math.ceil(entry.until - now));
    if (remaining <= 0) {
      expired.push(username);
      continue;
    }

    activeUsers.add(username);
    let card = timeoutCards.get(username);
    if (!card) {
      card = createCard(entry);
      timeoutCards.set(username, card);
      timeoutList.appendChild(card);
      ensureWalker(timeoutList, timeoutCards, timeoutWalkers, username, card);
    }
    updateCard(card, entry, `T ${remaining}s`);
  }

  for (const username of expired) {
    timeouts.delete(username);
    removeCard(timeoutCards, timeoutList, timeoutWalkers, username);
  }

  for (const username of Array.from(timeoutCards.keys())) {
    if (!activeUsers.has(username)) {
      removeCard(timeoutCards, timeoutList, timeoutWalkers, username);
    }
  }

}

function syncDeathRowCards() {
  const activeUsers = new Set();

  for (const [username, entry] of deathRow) {
    activeUsers.add(username);
    let card = deathRowCards.get(username);
    if (!card) {
      card = createCard(entry);
      deathRowCards.set(username, card);
      deathRowList.appendChild(card);
      ensureWalker(deathRowList, deathRowCards, deathRowWalkers, username, card);
    }
    updateCard(card, entry, "");
  }

  for (const username of Array.from(deathRowCards.keys())) {
    if (!activeUsers.has(username)) {
      removeCard(deathRowCards, deathRowList, deathRowWalkers, username);
    }
  }

}

function removeCard(cardMap, container, walkerMap, username) {
  const card = cardMap.get(username);
  if (card) {
    card.remove();
    cardMap.delete(username);
  }
  walkerMap.delete(username);
  const note = container.querySelector(".empty-note");
  if (note) {
    note.remove();
  }
}

function ensureWalker(container, cardMap, walkerMap, username, card) {
  if (walkerMap.has(username)) {
    return;
  }

  const cardSize = card.offsetWidth || 80;
  walkerMap.set(username, {
    x: findSpawnX(container, cardMap, walkerMap, username, cardSize),
    dir: Math.random() < 0.5 ? -1 : 1,
    speed: 28 + Math.random() * 32,
    nextFlipAt: performance.now() + 900 + Math.random() * 2200,
    phase: Math.random() * Math.PI * 2,
  });
}

function findSpawnX(container, cardMap, walkerMap, username, cardSize) {
  const laneWidth = Math.max(0, container.clientWidth - LANE_PADDING * 2);
  const maxX = Math.max(0, laneWidth - cardSize);
  if (maxX === 0) {
    return 0;
  }

  const occupied = [];
  for (const [other, state] of walkerMap) {
    if (other === username || !cardMap.has(other)) {
      continue;
    }
    occupied.push([state.x, state.x + cardSize]);
  }
  occupied.sort((a, b) => a[0] - b[0]);

  let cursor = 0;
  for (const [start, end] of occupied) {
    if (cursor + cardSize + CARD_GAP <= start) {
      return cursor;
    }
    cursor = Math.max(cursor, end + CARD_GAP);
    if (cursor > maxX) {
      return Math.random() * maxX;
    }
  }

  return Math.min(cursor, maxX);
}

function tickMotion() {
  const now = performance.now();
  const dt = Math.min((now - lastMotionTick) / 1000, 0.05);
  lastMotionTick = now;

  updateLaneMotion(timeoutList, timeoutCards, timeoutWalkers, now, dt);
  updateLaneMotion(deathRowList, deathRowCards, deathRowWalkers, now, dt);

  requestAnimationFrame(tickMotion);
}

function updateLaneMotion(container, cardMap, walkerMap, now, dt) {
  if (cardMap.size === 0) {
    return;
  }

  const cardSize = Math.max(40, container.clientHeight - LANE_PADDING * 2);
  const laneWidth = Math.max(0, container.clientWidth - LANE_PADDING * 2);
  const maxX = Math.max(0, laneWidth - cardSize);

  const walkers = [];
  for (const [username, card] of cardMap) {
    const walker = walkerMap.get(username);
    if (!walker) {
      continue;
    }

    if (now >= walker.nextFlipAt) {
      walker.dir *= -1;
      walker.nextFlipAt = now + 900 + Math.random() * 2200;
    }

    walker.x += walker.dir * walker.speed * dt;

    if (walker.x <= 0) {
      walker.x = 0;
      walker.dir = 1;
      walker.nextFlipAt = now + 900 + Math.random() * 2200;
    }
    if (walker.x >= maxX) {
      walker.x = maxX;
      walker.dir = -1;
      walker.nextFlipAt = now + 900 + Math.random() * 2200;
    }

    walkers.push({ username, card, walker });
  }

  walkers.sort((a, b) => a.walker.x - b.walker.x);
  for (let i = 0; i < walkers.length - 1; i += 1) {
    const current = walkers[i];
    const next = walkers[i + 1];
    const overlap = current.walker.x + cardSize + CARD_GAP - next.walker.x;
    if (overlap > 0) {
      const push = overlap / 2;
      current.walker.x = Math.max(0, current.walker.x - push);
      next.walker.x = Math.min(maxX, next.walker.x + push);
      current.walker.dir = -1;
      next.walker.dir = 1;
      current.walker.nextFlipAt = now + 900 + Math.random() * 2200;
      next.walker.nextFlipAt = now + 900 + Math.random() * 2200;
    }
  }

  for (const { card, walker } of walkers) {
    const bobY = Math.sin(now / 500 + walker.phase) * 3;
    card.style.transform = `translateX(${walker.x}px) translateY(${bobY}px)`;
  }
}

function createCard(entry) {
  const card = document.createElement("article");
  card.className = "prisoner-card";

  const avatar = document.createElement("img");
  avatar.className = "prisoner-avatar";
  avatar.src = entry.avatar_url;
  avatar.alt = `${entry.username} avatar`;

  const id = document.createElement("div");
  id.className = "prisoner-id";
  id.textContent = `${entry.user_id}`;

  card.append(avatar, id);
  return card;
}

function updateCard(card, entry, statusLabel) {
  const avatar = card.querySelector(".prisoner-avatar");
  const id = card.querySelector(".prisoner-id");

  if (avatar && avatar.src !== entry.avatar_url) {
    avatar.src = entry.avatar_url;
  }

  if (id) {
    const nextId = `${entry.user_id}`;
    if (id.textContent !== nextId) {
      id.textContent = nextId;
    }
  }

  let status = card.querySelector(".status-chip");
  if (statusLabel) {
    if (!status) {
      status = document.createElement("div");
      status.className = "status-chip";
      card.appendChild(status);
    }
    if (status.textContent !== statusLabel) {
      status.textContent = statusLabel;
    }
  } else if (status) {
    status.remove();
  }
}

function createExecutionPrisoner(entry) {
  const wrapper = document.createElement("article");
  wrapper.className = "execution-prisoner";

  const face = document.createElement("div");
  face.className = "execution-prisoner-face";

  const avatar = document.createElement("img");
  avatar.className = "prisoner-avatar";
  avatar.src = entry.avatar_url;
  avatar.alt = `${entry.username} avatar`;

  const id = document.createElement("div");
  id.className = "prisoner-id";
  id.textContent = `${entry.user_id}`;

  face.append(avatar);
  wrapper.append(face, id);
  return wrapper;
}

function runExecution(method, entry, actor) {
  const scene = document.createElement("div");
  const banner = document.createElement("div");
  banner.className = "execution-banner";
  banner.innerHTML = `<span class="actor">${actor}</span> ordered ${entry.username} to ${method}`;

  if (method === "catapult") {
    scene.className = "catapult-scene";

    const sky = document.createElement("div");
    sky.className = "execution-sky";

    const ground = document.createElement("div");
    ground.className = "catapult-ground";

    const rig = document.createElement("div");
    rig.className = "catapult-rig";
    rig.innerHTML = `
      <div class="catapult-wheel catapult-wheel-left"></div>
      <div class="catapult-wheel catapult-wheel-right"></div>
      <div class="catapult-frame"></div>
      <div class="catapult-arm">
        <div class="catapult-bucket"></div>
      </div>
    `;

    const victim = createExecutionPrisoner(entry);
    victim.classList.add("catapult-victim");

    scene.append(sky, ground, rig, victim);
  } else if (method === "plank") {
    scene.className = "plank-scene";

    const sky = document.createElement("div");
    sky.className = "execution-sky";

    const water = document.createElement("div");
    water.className = "plank-water";

    const dock = document.createElement("div");
    dock.className = "plank-dock";

    const plank = document.createElement("div");
    plank.className = "plank";

    const victim = createExecutionPrisoner(entry);
    victim.classList.add("plank-victim");

    const splash = document.createElement("div");
    splash.className = "splash";

    scene.append(sky, water, dock, plank, victim, splash);
  } else {
    return;
  }

  executionStage.append(scene, banner);
  window.setTimeout(() => {
    scene.remove();
    banner.remove();
  }, 5600);
}

function onMessage(event) {
  const payload = JSON.parse(event.data);

  switch (payload.type) {
    case "snapshot": {
      timeouts.clear();
      deathRow.clear();
      for (const entry of payload.timeouts) {
        timeouts.set(entry.username, entry);
      }
      for (const entry of payload.death_row) {
        deathRow.set(entry.username, entry);
      }
      render();
      break;
    }
    case "timeout_add": {
      timeouts.set(payload.entry.username, payload.entry);
      render();
      break;
    }
    case "timeout_remove": {
      timeouts.delete(payload.username);
      render();
      break;
    }
    case "ban_add": {
      const { username } = payload.entry;
      timeouts.delete(username);
      removeCard(timeoutCards, timeoutList, timeoutWalkers, username);
      deathRow.set(username, payload.entry);
      render();
      break;
    }
    case "ban_remove": {
      deathRow.delete(payload.username);
      render();
      break;
    }
    case "execute": {
      deathRow.delete(payload.entry.username);
      render();
      runExecution(payload.method, payload.entry, payload.actor);
      break;
    }
    default:
      break;
  }
}

function connect() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws/${channel}`);

  socket.addEventListener("message", onMessage);
  socket.addEventListener("close", () => {
    window.setTimeout(connect, 2000);
  });
  socket.addEventListener("open", () => {});
}

window.setInterval(render, 1000);
render();
connect();
requestAnimationFrame(tickMotion);
