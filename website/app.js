"use strict";

// This is a presentation demo. It never contacts the router or account APIs.
const demoAccounts = [...document.querySelectorAll(".demo-account")];
const demoQuotas = [22, 74, 45];
let owner = null;
const title = document.getElementById("demo-title");
const message = document.getElementById("demo-message");
const ownerChip = document.getElementById("chat-owner");
const startButton = document.getElementById("start-chat");
const limitButton = document.getElementById("simulate-limit");

function paintAccounts() {
  demoAccounts.forEach((card, index) => {
    card.classList.toggle("is-active", owner === index);
    card.querySelector(".account-remaining").textContent = `${demoQuotas[index]}%`;
    card.querySelector(".account-meter span").style.width = `${demoQuotas[index]}%`;
    card.querySelector(".account-status").textContent = demoQuotas[index] > 0 ? "left" : "at limit";
  });
  ownerChip.textContent = owner === null ? "Automatic" : owner === 0 ? "Primary" : `Account ${owner + 1}`;
}

startButton.addEventListener("click", () => {
  if (owner === null) {
    owner = demoQuotas.indexOf(Math.max(...demoQuotas));
    title.textContent = "A good place to start.";
    message.textContent = "Account 2 takes this example chat. Your follow-ups stay here while it has usage.";
    startButton.firstChild.textContent = "Send follow-up ";
    limitButton.disabled = false;
  } else {
    title.textContent = "Right where you left off.";
    message.textContent = `${owner === 0 ? "Primary" : `Account ${owner + 1}`} keeps this conversation. Follow-ups don't bounce between accounts.`;
  }
  paintAccounts();
});

limitButton.addEventListener("click", () => {
  if (owner === null) return;
  demoQuotas[owner] = 0;
  const best = Math.max(...demoQuotas);
  if (best > 0) {
    owner = demoQuotas.indexOf(best);
    title.textContent = "Same thread. More room.";
    message.textContent = `The previous account hit its limit. This chat continues on ${owner === 0 ? "Primary" : `Account ${owner + 1}`} with its history.`;
  } else {
    title.textContent = "Time for a breather.";
    message.textContent = "Every account is depleted. Router shows a combined limit alert; it doesn't create extra quota or spend a reset in Ask me mode.";
    startButton.disabled = true;
    limitButton.disabled = true;
  }
  paintAccounts();
});

document.getElementById("reset-demo").addEventListener("click", () => {
  owner = null;
  demoQuotas.splice(0, demoQuotas.length, 22, 74, 45);
  title.textContent = "Keep making things.";
  message.textContent = "Start a chat. See which subscription picks it up.";
  startButton.firstChild.textContent = "Start a chat ";
  startButton.disabled = false;
  limitButton.disabled = true;
  paintAccounts();
});

const tabs = [...document.querySelectorAll('[role="tab"]')];
function selectTab(tab) {
  tabs.forEach((item) => {
    const selected = item === tab;
    item.setAttribute("aria-selected", String(selected));
    item.tabIndex = selected ? 0 : -1;
    document.getElementById(item.getAttribute("aria-controls")).hidden = !selected;
  });
}
tabs.forEach((tab, index) => {
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", (event) => {
    let next;
    if (event.key === "ArrowRight") next = tabs[(index + 1) % tabs.length];
    if (event.key === "ArrowLeft") next = tabs[(index - 1 + tabs.length) % tabs.length];
    if (event.key === "Home") next = tabs[0];
    if (event.key === "End") next = tabs[tabs.length - 1];
    if (!next) return;
    event.preventDefault();
    selectTab(next);
    next.focus();
  });
});

document.querySelectorAll("[data-copy]").forEach((button) => {
  let feedbackTimer;
  button.addEventListener("click", async () => {
    const command = document.getElementById(button.dataset.copy);
    const status = document.getElementById("copy-status");
    clearTimeout(feedbackTimer);
    try {
      await navigator.clipboard.writeText(command.textContent);
      button.querySelector("span").textContent = "Copied";
      status.textContent = "Install command copied to clipboard.";
    } catch {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(command);
      selection.removeAllRanges();
      selection.addRange(range);
      button.querySelector("span").textContent = "Select & copy";
      status.textContent = "Automatic copying is unavailable. The command is selected; copy it manually.";
    }
    feedbackTimer = setTimeout(() => { button.querySelector("span").textContent = "Copy"; }, 2500);
  });
});
