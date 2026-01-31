(function initUpSet(){
  const container = document.getElementById('upset-container');
  if (!container) { setTimeout(initUpSet, 100); return; }

  if (!window.UpSetJS) { 
    console.warn("UpSetJS not loaded yet; retrying...");
    setTimeout(initUpSet, 200); 
    return; 
  }

  // Wait until trame state bridge exists
  if (!window.trame || !window.trame.state) {
    console.warn("window.trame.state not available yet; retrying...");
    setTimeout(initUpSet, 200);
    return;
  }

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

      window.trame.state.upset_click = {
        name: set.name,
        size: set.cardinality,
        ts: Date.now(),
      };

      if (window.trame.flushState) window.trame.flushState();
      if (window.trame.pushState) window.trame.pushState();
    },
  });

  console.log("UpSet rendered; clicks will update state.upset_click");
})();