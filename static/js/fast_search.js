// Simple category filtering with counts - NO search functionality
document.addEventListener("DOMContentLoaded", function () {
  const categoryButtons = document.querySelectorAll('.category-filter-btn');
  const clearButton = document.getElementById('clearCategoryFilters');
  const statusButtons = document.querySelectorAll('.status-filter-btn');
  const clearStatusButton = document.getElementById('clearStatusFilters');
  let activeFilters = new Set();
  let activeStatusFilters = new Set();
  
  // Use server-provided category counts for initial display
  function initializeCounts() {
    if (window.categoryCounts) {
      categoryButtons.forEach(btn => {
        const originalCategory = btn.dataset.category;
        const lowercaseCategory = originalCategory.toLowerCase();
        
        // Try both original case and lowercase versions
        const count = window.categoryCounts[originalCategory] || 
                     window.categoryCounts[lowercaseCategory] || 0;
        
        const badge = btn.querySelector('.category-count');
        if (badge) {
          badge.textContent = count;
        }
        
        // Debug for problematic categories
        if (originalCategory === 'Taizé' || originalCategory === 'k Duchu Svätému') {
          console.log(`initializeCounts - ${originalCategory}: found count ${count}`);
          console.log('Available keys:', Object.keys(window.categoryCounts).filter(k => 
            k.toLowerCase().includes(lowercaseCategory.substring(0, 4))
          ));
        }
        
        // Disable/enable button based on count
        if (count === 0) {
          btn.disabled = true;
          btn.style.opacity = '0.3';
          btn.style.pointerEvents = 'none';
        } else {
          btn.disabled = false;
          btn.style.opacity = '1';
          btn.style.pointerEvents = 'auto';
        }
      });
    }
  }
  
  function updateCounts() {
    // If no filters are active, show full database counts
    if (activeFilters.size === 0) {
      initializeCounts();
      return;
    }
    
    // For active filters, fetch updated counts from server
    fetchUpdatedCounts();
  }
  
  // Fetch updated category counts from server when filters are active
  async function fetchUpdatedCounts() {
    console.log('fetchUpdatedCounts called with activeFilters:', Array.from(activeFilters));
    try {
      const params = new URLSearchParams();
      if (activeFilters.size > 0) {
        params.append('active_categories', Array.from(activeFilters).join(','));
      }
      
      console.log('Fetching category counts with params:', params.toString());
      const response = await fetch(`/api/category_counts?${params}`);
      const counts = await response.json();
      console.log('Raw API response:', counts);
      
      // Debug logging for category matching
      console.log('API response keys:', Object.keys(counts));
      console.log('Button categories:', Array.from(categoryButtons).map(btn => btn.dataset.category.toLowerCase()));
      
      // Update count badges and disable empty categories
      categoryButtons.forEach(btn => {
        const originalCategory = btn.dataset.category;
        const lowercaseCategory = originalCategory.toLowerCase();
        
        // Try both original case and lowercase versions
        let count = counts[lowercaseCategory] || counts[originalCategory] || 0;
        
        const badge = btn.querySelector('.category-count');
        
        // Debug specific categories that are problematic
        if (originalCategory === 'Taizé' || originalCategory === 'k Duchu Svätému') {
          console.log(`Category: ${originalCategory}, lowercase: ${lowercaseCategory}, count: ${count}`);
          console.log('Available keys containing this category:', Object.keys(counts).filter(k => 
            k.toLowerCase().includes(lowercaseCategory.substring(0, 5))
          ));
        }
        
        if (badge) {
          badge.textContent = count;
        }
        
        // Disable/enable button based on count
        if (count === 0) {
          btn.disabled = true;
          btn.style.opacity = '0.3';
          btn.style.pointerEvents = 'none';
        } else {
          btn.disabled = false;
          btn.style.opacity = '1';
          btn.style.pointerEvents = 'auto';
        }
      });
      
    } catch (error) {
      console.error('Error fetching updated category counts:', error);
      // Fallback to old method if API fails
      updateCountsOldMethod();
    }
  }
  
  // Fallback method - count visible songs (old method)
  function updateCountsOldMethod() {
    const tableRows = document.querySelectorAll("tbody tr.song-row");
    const mobileCards = document.querySelectorAll(".mobile-song-card.song-row");
    const counts = {};
    
    // Count only visible songs in each category when filters are active
    [...tableRows, ...mobileCards].forEach(element => {
      if (element.style.display !== 'none') {
        const categories = (element.dataset.categories || '').toLowerCase();
        categoryButtons.forEach(btn => {
          const category = btn.dataset.category.toLowerCase();
          if (categories.includes(category)) {
            counts[category] = (counts[category] || 0) + 1;
          }
        });
      }
    });
    
    // Update count badges and disable empty categories
    categoryButtons.forEach(btn => {
      const category = btn.dataset.category.toLowerCase();
      const count = counts[category] || 0;
      const badge = btn.querySelector('.category-count');
      if (badge) {
        badge.textContent = count;
      }
      
      // Disable/enable button based on count
      if (count === 0) {
        btn.disabled = true;
        btn.style.opacity = '0.3';
        btn.style.pointerEvents = 'none';
      } else {
        btn.disabled = false;
        btn.style.opacity = '1';
        btn.style.pointerEvents = 'auto';
      }
    });
  }
  
  function filterRows() {
    const tableRows = document.querySelectorAll("tbody tr.song-row");
    const mobileCards = document.querySelectorAll(".mobile-song-card.song-row");
    
    // If no filters are active, show all rows
    if (activeFilters.size === 0 && activeStatusFilters.size === 0) {
      tableRows.forEach(row => row.style.display = "");
      mobileCards.forEach(card => card.style.display = "");
      updateCounts();
      return;
    }
    
    const categoryFilters = Array.from(activeFilters);
    const statusFilters = Array.from(activeStatusFilters);
    
    // Filter table rows
    tableRows.forEach(row => {
      let show = true;
      
      // Check category filters (must have ALL selected categories)
      if (categoryFilters.length > 0) {
        const categories = (row.dataset.categories || '').toLowerCase();
        show = categoryFilters.every(f => categories.includes(f.toLowerCase()));
      }
      
      // Check status filters (must match ALL selected statuses)
      if (show && statusFilters.length > 0) {
        statusFilters.forEach(statusFilter => {
          if (statusFilter === 'unchecked') {
            show = show && (row.dataset.checked === 'false');
          } else if (statusFilter === 'unprinted') {
            show = show && (row.dataset.printed === 'false');
          }
        });
      }
      
      row.style.display = show ? "" : "none";
    });
    
    // Filter mobile cards
    mobileCards.forEach(card => {
      let show = true;
      
      // Check category filters (must have ALL selected categories)
      if (categoryFilters.length > 0) {
        const categories = (card.dataset.categories || '').toLowerCase();
        show = categoryFilters.every(f => categories.includes(f.toLowerCase()));
      }
      
      // Check status filters (must match ALL selected statuses)
      if (show && statusFilters.length > 0) {
        statusFilters.forEach(statusFilter => {
          if (statusFilter === 'unchecked') {
            show = show && (card.dataset.checked === 'false');
          } else if (statusFilter === 'unprinted') {
            show = show && (card.dataset.printed === 'false');
          }
        });
      }
      
      card.style.display = show ? "" : "none";
    });
    
    updateCounts();
  }
  
  categoryButtons.forEach(btn => {
    btn.addEventListener('click', function() {
      const category = this.dataset.category;
      if (activeFilters.has(category)) {
        activeFilters.delete(category);
        this.classList.remove('active');
      } else {
        activeFilters.add(category);
        this.classList.add('active');
      }
      if (clearButton) clearButton.style.display = activeFilters.size > 0 ? 'block' : 'none';
      
      // If all filters are empty, refresh the whole page to reset everything
      if (activeFilters.size === 0 && activeStatusFilters.size === 0) {
        console.log('All filters cleared - refreshing page to reset table');
        window.location.reload();
        return;
      }
      
      filterRows();
      
      // Update counts immediately after filter change
      console.log('Category filter changed, updating counts...');
      updateCounts();
    });
  });
  
  if (clearButton) {
    clearButton.addEventListener('click', function() {
      activeFilters.clear();
      categoryButtons.forEach(btn => btn.classList.remove('active'));
      this.style.display = 'none';
      
      // If all filters are empty, refresh the whole page to reset everything
      if (activeFilters.size === 0 && activeStatusFilters.size === 0) {
        console.log('All filters cleared - refreshing page to reset table');
        window.location.reload();
        return;
      }
      
      filterRows();
      
      // Update counts after clearing filters
      console.log('Filters cleared, updating counts...');
      updateCounts();
    });
  }
  
  // Status filter event handlers
  statusButtons.forEach(btn => {
    btn.addEventListener('click', function() {
      const status = this.dataset.status;
      if (activeStatusFilters.has(status)) {
        activeStatusFilters.delete(status);
        this.classList.remove('active');
      } else {
        activeStatusFilters.add(status);
        this.classList.add('active');
      }
      
      // Update global window.activeStatusFilters for pagination system
      if (!window.activeStatusFilters) window.activeStatusFilters = new Set();
      if (activeStatusFilters.has(status)) {
        window.activeStatusFilters.add(status);
      } else {
        window.activeStatusFilters.delete(status);
      }
      
      if (clearStatusButton) clearStatusButton.style.display = activeStatusFilters.size > 0 ? 'block' : 'none';
      
      // If all filters are empty, refresh the whole page to reset everything
      if (activeFilters.size === 0 && activeStatusFilters.size === 0) {
        console.log('All filters cleared - refreshing page to reset table');
        window.location.reload();
        return;
      }
      
      filterRows();
      
      // Update counts immediately after filter change
      console.log('Status filter changed, updating counts...');
      updateCounts();
    });
  });
  
  if (clearStatusButton) {
    clearStatusButton.addEventListener('click', function() {
      activeStatusFilters.clear();
      
      // Clear global window.activeStatusFilters for pagination system
      if (window.activeStatusFilters) {
        window.activeStatusFilters.clear();
      }
      
      statusButtons.forEach(btn => btn.classList.remove('active'));
      this.style.display = 'none';
      
      // If all filters are empty, refresh the whole page to reset everything
      if (activeFilters.size === 0 && activeStatusFilters.size === 0) {
        console.log('All filters cleared - refreshing page to reset table');
        window.location.reload();
        return;
      }
      
      filterRows();
      
      // Update counts after clearing status filters
      console.log('Status filters cleared, updating counts...');
      updateCounts();
    });
  }
  
  // Initialize status filter counts
  function initializeStatusCounts() {
    const uncheckedCount = document.getElementById('uncheckedCount');
    const unprintedCount = document.getElementById('unprintedCount');
    
    if (uncheckedCount && unprintedCount && window.statusCounts) {
      uncheckedCount.textContent = window.statusCounts.unchecked;
      unprintedCount.textContent = window.statusCounts.unprinted;
    }
  }
  
  // Initialize counts on page load with server data
  initializeCounts();
  initializeStatusCounts();
  
  // Make fetchUpdatedCounts available globally for pagination module
  window.fetchUpdatedCounts = fetchUpdatedCounts;
});
