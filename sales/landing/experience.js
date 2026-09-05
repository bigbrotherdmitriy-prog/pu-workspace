(() => {
  "use strict";

  const root = document.documentElement;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const depthElements = Array.from(document.querySelectorAll("[data-depth]"));
  const hero = document.querySelector(".hero");
  let frame = 0;

  const revealGroups = [
    ".manifest-grid",
    ".context-source",
    ".values-heading",
    ".values-deck article",
    ".story-head",
    ".timeline article",
    ".comparison-head",
    ".comparison-row",
    ".film-grid",
    ".scenario-intro",
    ".scenario-list a",
    ".result-grid > *",
    ".dev-lab-grid > *",
    ".purchase-head",
    ".purchase-grid article",
    ".faq-grid > *",
    ".access-panel",
  ];

  const revealElements = revealGroups.flatMap((selector) =>
    Array.from(document.querySelectorAll(selector)),
  );
  revealElements.forEach((element, index) => {
    element.dataset.reveal = "";
    element.dataset.revealDelay = String(index % 4);
  });

  if ("IntersectionObserver" in window && !reducedMotion.matches) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      });
    }, { rootMargin: "0px 0px -9%", threshold: 0.08 });
    revealElements.forEach((element) => observer.observe(element));
  } else {
    revealElements.forEach((element) => element.classList.add("is-visible"));
  }

  function renderScroll() {
    frame = 0;
    const maximum = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
    const progress = Math.min(1, Math.max(0, window.scrollY / maximum));
    root.style.setProperty("--scroll-progress", progress.toFixed(4));
    if (reducedMotion.matches) return;
    depthElements.forEach((element) => {
      const depth = Number(element.dataset.depth || 0);
      element.style.setProperty("--parallax-y", String(Math.round(window.scrollY * depth * -0.24)));
    });
  }

  function requestRender() {
    if (!frame) frame = window.requestAnimationFrame(renderScroll);
  }

  function resetMotion() {
    if (reducedMotion.matches) {
      depthElements.forEach((element) => element.style.removeProperty("--parallax-y"));
      root.style.setProperty("--pointer-x", "0");
      root.style.setProperty("--pointer-y", "0");
      revealElements.forEach((element) => element.classList.add("is-visible"));
    }
    requestRender();
  }

  hero?.addEventListener("pointermove", (event) => {
    if (reducedMotion.matches || event.pointerType === "touch") return;
    const bounds = hero.getBoundingClientRect();
    const x = ((event.clientX - bounds.left) / bounds.width - 0.5) * 12;
    const y = ((event.clientY - bounds.top) / bounds.height - 0.5) * 9;
    root.style.setProperty("--pointer-x", x.toFixed(2));
    root.style.setProperty("--pointer-y", y.toFixed(2));
  });
  hero?.addEventListener("pointerleave", () => {
    root.style.setProperty("--pointer-x", "0");
    root.style.setProperty("--pointer-y", "0");
  });

  window.addEventListener("scroll", requestRender, { passive: true });
  window.addEventListener("resize", requestRender, { passive: true });
  reducedMotion.addEventListener?.("change", resetMotion);
  renderScroll();
})();
