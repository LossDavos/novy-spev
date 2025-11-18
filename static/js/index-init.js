// Index page initialization and utilities
document.addEventListener("DOMContentLoaded", function () {
  // Initialize JavaScript wheel animation on the navbar brand
  const suffixes = ['ť', 'ať', 'iť', 'úť', 'eť', 'ľ', 'ď', 'sť'];
  const brandText = document.querySelector('.navbar-brand .wheel-container .wheel-text');
  
  if (brandText) {
    let currentIndex = 0;
    setInterval(() => {
      brandText.style.opacity = '0';
      setTimeout(() => {
        brandText.textContent = suffixes[currentIndex];
        brandText.style.opacity = '1';
        currentIndex = (currentIndex + 1) % suffixes.length;
      }, 200);
    }, 1500);
  }

  // Category filters collapse chevron rotation
  const categoryFiltersCollapse = document.getElementById('categoryFiltersCollapse');
  const categoryChevron = document.getElementById('categoryChevron');
  
  if (categoryFiltersCollapse && categoryChevron) {
    categoryFiltersCollapse.addEventListener('show.bs.collapse', function () {
      categoryChevron.classList.remove('bi-chevron-down');
      categoryChevron.classList.add('bi-chevron-up');
    });
    
    categoryFiltersCollapse.addEventListener('hide.bs.collapse', function () {
      categoryChevron.classList.remove('bi-chevron-up');
      categoryChevron.classList.add('bi-chevron-down');
    });
  }

  // Refresh button functionality
  const refreshButton = document.getElementById('refreshButton');
  if (refreshButton) {
    refreshButton.addEventListener('click', function() {
      this.innerHTML = '<i class="spinner-border spinner-border-sm" role="status"></i> Obnovujem...';
      this.disabled = true;
      
      // Refresh after a short delay
      setTimeout(() => {
        window.location.reload();
      }, 500);
    });
  }
  
  // Debug button functionality
  const debugButton = document.getElementById('debugLoadButton');
  if (debugButton) {
    debugButton.addEventListener('click', function() {
      console.log('Debug Load Button Clicked!');
      console.log('Pagination State:', {
        currentlyLoadedSongs: window.CURRENTLY_LOADED_SONGS,
        totalSongs: window.TOTAL_SONGS,
        initialBatchSize: window.INITIAL_BATCH_SIZE,
        isSearchActive: window.isSearchActive || false,
        activeFilters: window.activeFilters || 'not initialized'
      });
    });
  }
});

// Global utilities
window.setDisplayMode = function(mode) {
  const desktopView = document.getElementById('desktopView');
  const mobileView = document.getElementById('mobileView');
  const displayToggle = document.getElementById('displayToggle');
  
  if (mode === 'mobile') {
    desktopView.style.display = 'none';
    mobileView.style.display = 'block';
    displayToggle.textContent = 'Prepnúť na tabuľku';
  } else {
    desktopView.style.display = 'block';
    mobileView.style.display = 'none';
    displayToggle.textContent = 'Prepnúť na karty';
  }
};

// Theme management functions
window.setTheme = function(theme) {
  document.body.className = theme;
  localStorage.setItem('theme', theme);
};

window.initializeTheme = function() {
  const savedTheme = localStorage.getItem('theme') || 'theme-default';
  window.setTheme(savedTheme);
};