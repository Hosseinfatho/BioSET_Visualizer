(function initUpSet() {
  const container = document.getElementById('upset-container');
  if (!container) {
    setTimeout(initUpSet, 100);
    return;
  }

  if (!window.UpSetJS) {
    setTimeout(initUpSet, 200);
    return;
  }

  if (!window.trame || !window.trame.state) {
    setTimeout(initUpSet, 200);
    return;
  }

  // Sample data - will be replaced with real data later
  const elems = [
    { name: 'E1', sets: ['Ch1'] },
    { name: 'E2', sets: ['Ch1', 'Ch2'] },
    { name: 'E3', sets: ['Ch1', 'Ch2'] },
    { name: 'E4', sets: ['Ch1', 'Ch2', 'Ch3'] },
  ];

  const { sets, combinations } = UpSetJS.extractCombinations(elems);

  container.innerHTML = "";
  UpSetJS.render(container, {
    sets,
    combinations,
    width: 330,
    height: 280,
    theme: 'dark',
    onClick: (set) => {
      if (!set) return;
      
      window.trame.state.set("upset_click", {
        name: set.name,
        size: set.cardinality,
        ts: Date.now(),
      });
    },
  });
})();