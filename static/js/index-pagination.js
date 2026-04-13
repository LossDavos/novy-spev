// Mobile expand/collapse functionality
document.addEventListener("DOMContentLoaded", function () {

  // Mobile expand/collapse functionality
  document.querySelectorAll('.mobile-expand-toggle').forEach(toggle => {
    toggle.addEventListener('click', function() {
      const isExpanded = this.getAttribute('aria-expanded') === 'true';
      this.classList.toggle('expanded', !isExpanded);
    });
  });

  // Handle collapse events for mobile cards
  document.querySelectorAll('[data-bs-toggle="collapse"]').forEach(trigger => {
    const targetId = trigger.getAttribute('data-bs-target');
    const target = document.querySelector(targetId);

    if (target) {
      target.addEventListener('show.bs.collapse', () => {
        trigger.classList.add('expanded');
        trigger.setAttribute('aria-expanded', 'true');
      });

      target.addEventListener('hide.bs.collapse', () => {
        trigger.classList.remove('expanded');
        trigger.setAttribute('aria-expanded', 'false');
      });
    }
  });

  // Initialize Bootstrap tooltips for status icons
  const tooltipTriggerList = document.querySelectorAll('[data-bs-toggle="tooltip"]');
  const tooltipList = [...tooltipTriggerList].map(tooltipTriggerEl => new bootstrap.Tooltip(tooltipTriggerEl));

  // Initialize main page lazy loading
  initializeMainPageLazyLoading();

});

// Main Page Lazy Loading Implementation
function initializeMainPageLazyLoading() {
  const tableBody = document.getElementById('songsTableBody');
  const mobileContainer = document.getElementById('mobileSongsContainer');
  const tableLoadingIndicator = document.getElementById('tableLoadingIndicator');
  const mobileLoadingIndicator = document.getElementById('mobileLoadingIndicator');
  const loadedSongsCount = document.getElementById('loadedSongsCount');
  const totalSongsCount = document.getElementById('totalSongsCount');
  const allSongsLoaded = document.getElementById('allSongsLoaded');
  const fileSelectionModalEl = document.getElementById('fileSelectionModal');
  const fileSelectionModalHeader = document.getElementById('fileSelectionModalHeader');
  const fileSelectionModalIcon = document.getElementById('fileSelectionModalIcon');
  const fileSelectionModalTitle = document.getElementById('fileSelectionModalTitle');
  const fileSelectionModalBody = document.getElementById('fileSelectionModalBody');
  const fileSelectionModal = fileSelectionModalEl ? new bootstrap.Modal(fileSelectionModalEl) : null;

  const initialBatchSize = window.INITIAL_BATCH_SIZE;
  const totalSongs = window.TOTAL_SONGS;
  let currentlyLoadedSongs = window.CURRENTLY_LOADED_SONGS;
  let isLoading = false;
  let allLoaded = currentlyLoadedSongs >= totalSongs;
  let activeFilters = new Set(); // Track active category filters
  let totalFilteredSongs = totalSongs; // Track total songs with current filters

  // Make activeFilters globally accessible for search integration
  window.activeFilters = activeFilters;

  // Debug initial state
  console.log('Pagination initialized:', {
    initialBatchSize,
    totalSongs,
    currentlyLoadedSongs,
    allLoaded
  });

  // Function to reset pagination state (called when clearing search)
  window.resetPaginationState = function() {
    currentlyLoadedSongs = window.CURRENTLY_LOADED_SONGS;
    isLoading = false;
    allLoaded = currentlyLoadedSongs >= totalSongs;
    totalFilteredSongs = totalSongs;
    activeFilters.clear();
    if (window.activeStatusFilters) {
      window.activeStatusFilters.clear();
    }
    console.log('Pagination state reset to initial values');
  };

  function decodeFilePaths(encodedPaths) {
    if (!encodedPaths) return [];
    try {
      const parsed = JSON.parse(decodeURIComponent(encodedPaths));
      return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
      console.error('Unable to decode file paths:', error);
      return [];
    }
  }

  function openFileListModal(kind, songId, encodedPaths, songTitle) {
    if (!fileSelectionModal || !fileSelectionModalTitle || !fileSelectionModalBody || !fileSelectionModalHeader || !fileSelectionModalIcon) return;

    const files = decodeFilePaths(encodedPaths);
    const isMp3 = kind === 'mp3';

    fileSelectionModalHeader.classList.remove('bg-success', 'bg-danger', 'text-white');
    fileSelectionModalHeader.classList.add(isMp3 ? 'bg-success' : 'bg-danger', 'text-white');
    fileSelectionModalIcon.className = isMp3 ? 'bi bi-music-note-list me-2' : 'bi bi-file-music me-2';

    const cleanSongTitle = (songTitle || '').trim();
    fileSelectionModalTitle.textContent = isMp3
      ? `MP3 Súbory${cleanSongTitle ? ` - ${cleanSongTitle}` : ''}`
      : `Noty${cleanSongTitle ? ` - ${cleanSongTitle}` : ''}`;

    if (!files.length) {
      fileSelectionModalBody.innerHTML = '<p class="text-muted mb-0">Žiadne súbory.</p>';
      fileSelectionModal.show();
      return;
    }

    const itemsHtml = files.map(filePath => {
      const filename = filePath.split('/').pop() || filePath;
      const href = isMp3
        ? `/api/presigned_url?key=${encodeURIComponent(filePath)}`
        : `/song/${songId}/download_sheet/${encodeURIComponent(filename)}`;

      return `
        <a href="${href}"
           target="_blank"
           class="list-group-item list-group-item-action d-flex align-items-center gap-3">
          <i class="${isMp3 ? 'bi bi-play-circle-fill text-success fs-4' : 'bi bi-file-pdf text-danger fs-4'}"></i>
          <span class="flex-grow-1 min-w-0 text-break">${escapeHtml(filename)}</span>
          <i class="bi bi-box-arrow-up-right text-muted"></i>
        </a>
      `;
    }).join('');

    fileSelectionModalBody.innerHTML = `<div class="list-group">${itemsHtml}</div>`;
    fileSelectionModal.show();
  }

  document.addEventListener('click', function(event) {
    const trigger = event.target.closest('.js-open-file-modal');
    if (!trigger) return;

    event.preventDefault();
    openFileListModal(
      trigger.getAttribute('data-file-kind'),
      trigger.getAttribute('data-song-id'),
      trigger.getAttribute('data-file-paths'),
      trigger.getAttribute('data-song-title')
    );
  });

  // Load initial batch from server data
  if (window.INITIAL_SONGS && window.INITIAL_SONGS.length > 0) {
    renderInitialSongs(window.INITIAL_SONGS);
  }

  // Listen for category filter changes
  setupCategoryFilterListener();

  // Set up scroll listeners for both desktop and mobile
  const mainTable = document.getElementById('mainSongsTable');
  const mobileContainerEl = document.getElementById('mobileSongsContainer');

  // Desktop scroll listener - use window scroll instead of table scroll
  window.addEventListener('scroll', throttle(() => {
    if (window.innerWidth > 991) { // Desktop breakpoint
      if (shouldLoadMoreWindow()) {
        loadMoreSongs();
      }
    } else { // Mobile breakpoint
      if (shouldLoadMoreWindow()) {
        loadMoreSongs();
      }
    }
  }, 200));

  // Additional check for table container scroll (in case the table itself is scrollable)
  if (mainTable) {
    mainTable.addEventListener('scroll', throttle(() => {
      if (window.innerWidth > 991 && shouldLoadMore(mainTable)) {
        loadMoreSongs();
      }
    }, 200));
  }

  // Render initial songs from server
  function renderInitialSongs(songs) {
    songs.forEach(song => {
      renderDesktopSong(song);
      renderMobileSong(song);
    });
    updateLoadedCount();
  }

  // Check if should load more (desktop table)
  function shouldLoadMore(container) {
    if (isLoading || allLoaded) return false;
    const scrollTop = container.scrollTop;
    const scrollHeight = container.scrollHeight;
    const clientHeight = container.clientHeight;
    return scrollTop + clientHeight >= scrollHeight - 300; // Load when 300px from bottom
  }

  // Check if should load more (window scroll)
  function shouldLoadMoreWindow() {
    if (isLoading || allLoaded) return false;
    const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    const documentHeight = document.documentElement.scrollHeight || document.body.scrollHeight;
    const windowHeight = window.innerHeight;

    // More aggressive loading - start loading when 800px from bottom
    const threshold = 800;
    const shouldLoad = scrollTop + windowHeight >= documentHeight - threshold;

    // Debug logging
    if (shouldLoad) {
      const displayTotal = activeFilters.size > 0 ? totalFilteredSongs : totalSongs;
      console.log('Scroll trigger:', {
        scrollTop,
        windowHeight,
        documentHeight,
        threshold,
        distanceFromBottom: documentHeight - (scrollTop + windowHeight),
        currentlyLoaded: currentlyLoadedSongs,
        totalSongs: displayTotal,
        activeFilters: Array.from(activeFilters),
        allLoaded
      });
    }

    return shouldLoad;
  }

  // Setup category and status filter listeners
  function setupCategoryFilterListener() {
    // Get reference to the category filter buttons from the fast_search.js module
    const categoryButtons = document.querySelectorAll('.category-filter-btn');
    const statusButtons = document.querySelectorAll('.status-filter-btn');

    // Listen for clicks on category buttons
    categoryButtons.forEach(btn => {
      btn.addEventListener('click', function() {
        // Small delay to let the fast_search.js handle the filtering first
        setTimeout(() => {
          updateActiveFilters();
          resetPaginationForFilters();
        }, 100);
      });
    });

    // Listen for clicks on status buttons
    statusButtons.forEach(btn => {
      btn.addEventListener('click', function() {
        // Small delay to let the fast_search.js handle the filtering first
        setTimeout(() => {
          updateActiveFilters();
          resetPaginationForFilters();
        }, 100);
      });
    });

    // Listen for clear category filters button
    const clearButton = document.getElementById('clearCategoryFilters');
    if (clearButton) {
      clearButton.addEventListener('click', function() {
        setTimeout(() => {
          updateActiveFilters();
          resetPaginationForFilters();
        }, 100);
      });
    }

    // Listen for clear status filters button
    const clearStatusButton = document.getElementById('clearStatusFilters');
    if (clearStatusButton) {
      clearStatusButton.addEventListener('click', function() {
        setTimeout(() => {
          updateActiveFilters();
          resetPaginationForFilters();
        }, 100);
      });
    }
  }

  // Update active filters based on UI state
  function updateActiveFilters() {
    const activeCategoryBtns = document.querySelectorAll('.category-filter-btn.active');
    const activeStatusBtns = document.querySelectorAll('.status-filter-btn.active');

    activeFilters.clear();
    activeCategoryBtns.forEach(btn => {
      activeFilters.add(btn.dataset.category);
    });

    // Also track status filters for pagination
    window.activeStatusFilters = new Set();
    activeStatusBtns.forEach(btn => {
      window.activeStatusFilters.add(btn.dataset.status);
    });

    console.log('Active filters updated:', Array.from(activeFilters));
    console.log('Active status filters updated:', Array.from(window.activeStatusFilters));

    // If search is active, update search results with new filters
    if (window.isSearchActive) {
      const searchInput = document.getElementById('quickSearchInput');
      if (searchInput && searchInput.value.trim()) {
        console.log('Updating search results with new filters');
        // Trigger search update by calling the search function directly
        if (typeof window.triggerLiveSearchUpdate === 'function') {
          window.triggerLiveSearchUpdate(searchInput.value.trim());
        }
      }
    }
  }

  // Reset pagination when filters change
  function resetPaginationForFilters() {
    // If search is active, don't use pagination system - let search handle it
    if (window.isSearchActive) {
      console.log('Skipping pagination reset - search is handling filter updates');
      return;
    }

    // If we have active filters, we need to clear the loaded songs and reload with filters
    const hasStatusFilters = window.activeStatusFilters && window.activeStatusFilters.size > 0;
    if (activeFilters.size > 0 || hasStatusFilters) {
      console.log('Resetting pagination due to active filters');

      // Clear currently rendered songs
      tableBody.innerHTML = '';
      mobileContainer.innerHTML = '';

      // Reset pagination state
      currentlyLoadedSongs = 0;
      allLoaded = false;
      totalFilteredSongs = 0;

      // Load first batch with filters
      loadMoreSongs();

      // Update category counts with server data after a short delay
      setTimeout(() => {
        if (typeof window.fetchUpdatedCounts === 'function') {
          window.fetchUpdatedCounts();
        }
      }, 500);

    } else {
      console.log('No active filters, keeping current pagination');
      // No filters - restore original pagination state
      totalFilteredSongs = totalSongs;

      // If search is active, let search system handle the "no filters" case
      if (window.isSearchActive) {
        const searchInput = document.getElementById('quickSearchInput');
        if (searchInput && searchInput.value.trim()) {
          // Re-run search without filters
          if (typeof window.triggerLiveSearchUpdate === 'function') {
            window.triggerLiveSearchUpdate(searchInput.value.trim());
          }
        } else {
          // No search term, return to original list
          if (typeof window.returnToFilteredView === 'function') {
            window.returnToFilteredView();
          }
        }
        return; // Don't do the normal pagination reset
      }

      // Check if we need to reset (only when not in search mode)
      if (currentlyLoadedSongs > initialBatchSize) {
        // We've loaded extra filtered songs, need to reload
        // But don't reload if search is active - it would clear search input
        if (!window.isSearchActive) {
          location.reload(); // Simple reload to restore original state
        } else {
          console.log('Skipping page reload because search is active');
          // Instead, let the search system handle returning to unfiltered state
          if (typeof window.returnToFilteredView === 'function') {
            window.returnToFilteredView();
          }
        }
      }
    }
  }

  // Load more songs from server
  async function loadMoreSongs() {
    if (isLoading || allLoaded) {
      console.log('Skipping load more - isLoading:', isLoading, 'allLoaded:', allLoaded);
      return;
    }

    // Skip loading more songs if search is active
    if (window.isSearchActive) {
      console.log('Skipping load more - search is active');
      return;
    }

    console.log('Loading more songs - currentlyLoaded:', currentlyLoadedSongs, 'totalFiltered:', totalFilteredSongs, 'activeFilters:', Array.from(activeFilters));

    isLoading = true;
    showLoadingIndicators();

    try {
      let url, response, data;

      // Use different API depending on whether we have active filters
      if (activeFilters.size > 0 || (window.activeStatusFilters && window.activeStatusFilters.size > 0)) {
        // Use search API with category and status filters
        const params = new URLSearchParams();
        if (activeFilters.size > 0) {
          params.append('categories', Array.from(activeFilters).join(','));
        }
        if (window.activeStatusFilters && window.activeStatusFilters.has('unchecked')) {
          params.append('unchecked', 'true');
        }
        if (window.activeStatusFilters && window.activeStatusFilters.has('unprinted')) {
          params.append('printed', 'false');
        }
        params.append('offset', currentlyLoadedSongs.toString());
        params.append('limit', '25');

        url = `/api/search?${params}`;
        console.log('Fetching with filters:', url);

        response = await fetch(url);
        const searchData = await response.json();

        // Convert search API response to match songs API format
        data = {
          songs: searchData.results || [],
          total_songs: searchData.total_found || 0,
          has_more: searchData.has_more || false
        };

        // Update total filtered songs count
        if (currentlyLoadedSongs === 0) {
          totalFilteredSongs = searchData.total_found || 0;
        }

      } else {
        // Use regular songs API
        url = `/api/songs?offset=${currentlyLoadedSongs}&limit=25`;
        console.log('Fetching without filters:', url);

        response = await fetch(url);
        data = await response.json();
        totalFilteredSongs = data.total_songs;
      }

      console.log('API response:', data);

      if (data.songs && data.songs.length > 0) {
        console.log('Adding', data.songs.length, 'new songs');

        data.songs.forEach(song => {
          renderDesktopSong(song);
          renderMobileSong(song);
        });

        currentlyLoadedSongs += data.songs.length;
        allLoaded = !data.has_more || currentlyLoadedSongs >= totalFilteredSongs;

        console.log('Updated counts - currentlyLoaded:', currentlyLoadedSongs, 'allLoaded:', allLoaded);

        updateLoadedCount();

        // Update category counts after loading new songs
        // For filtered loads, we don't need to update counts since they're server-based
        // Only update for non-filtered loads
        if (typeof updateCounts === 'function' && activeFilters.size === 0) {
          updateCounts();
        }
      } else {
        console.log('No more songs returned');
        allLoaded = true;
      }

    } catch (error) {
      console.error('Error loading more songs:', error);
      allLoaded = true; // Stop trying to load more on error
    } finally {
      isLoading = false;
      hideLoadingIndicators();
    }
  }

  // Render desktop table row
  function renderDesktopSong(song) {
    const row = document.createElement('tr');
    row.id = `song-${song.id}`;
    row.className = 'song-row align-middle';
    row.setAttribute('data-categories', (song.categories || '').toLowerCase());
    row.setAttribute('data-printed', song.printed ? 'true' : 'false');
    row.setAttribute('data-checked', song.admin_checked ? 'true' : 'false');

    // Build alternative titles display
    let alternativeTitlesHtml = '';
    if (song.alternative_titles) {
      const altTitles = song.alternative_titles.split(';;').filter(t => t.trim());
      if (altTitles.length > 0) {
        alternativeTitlesHtml = `
          <div class="text-muted small">
            <i class="bi bi-arrow-repeat me-1"></i>
            ${altTitles.map(title => `<span class="badge bg-light text-dark me-1">${escapeHtml(title.trim())}</span>`).join('')}
          </div>`;
      }
    }

    // Build MP3 files display
    let mp3Html = '';
    if (song.mp3_paths && song.mp3_paths.length > 0) {
      const encodedMp3Paths = encodeURIComponent(JSON.stringify(song.mp3_paths));
      const mp3Label = song.mp3_paths.length === 1 ? 'MP3' : `${song.mp3_paths.length}× MP3`;
      mp3Html = `
        <button type="button"
                class="btn btn-success btn-sm d-flex align-items-center justify-content-center gap-1 js-open-file-modal"
                data-file-kind="mp3"
                data-song-id="${song.id}"
                data-file-paths="${encodedMp3Paths}"
                data-song-title="${escapeHtml(song.title || '')}"
                title="Zobraziť MP3 súbory">
          <i class="bi bi-volume-up-fill"></i>
          <span class="small">${mp3Label}</span>
        </button>`;
    } else {
      mp3Html = `
        <span class="text-muted small">
          <i class="bi bi-volume-mute"></i><br>Žiadne MP3
        </span>`;
    }

    // Build status badges
    const statusBadges = [];
    if (song.admin_checked) {
      statusBadges.push(`
        <span class="badge badge-checked px-3 py-2 shadow-sm" title="Pieseň je skontrolovaná">
          <i class="bi bi-check-circle-fill"></i>
        </span>`);
    } else {
      statusBadges.push(`
        <span class="badge badge-pending px-3 py-2 shadow-sm pulse-subtle" title="Pieseň čaká na kontrolu">
          <i class="bi bi-exclamation-triangle-fill"></i>
        </span>`);
    }
    if (song.printed) {
      statusBadges.push(`
        <span class="badge badge-printed px-3 py-2 shadow-sm" title="Pieseň je už vytlačená">
          <i class="bi bi-printer-fill"></i>
        </span>`);
    }

    // Build file download buttons
    let fileDownloadsHtml = '';
    if (song.pdf_lyrics_path || song.pdf_chords_path) {
      const lyricsBtn = song.pdf_lyrics_path ?
        `<a href="/uploads/${song.pdf_lyrics_path}"
           class="btn btn-outline-primary btn-sm"
           title="Stiahnuť text piesne">
          <i class="bi bi-file-text"></i> Text
        </a>` : '';

      const chordsBtn = song.pdf_chords_path ?
        `<a href="/uploads/${song.pdf_chords_path}"
           class="btn btn-outline-warning btn-sm"
           title="Stiahnuť akordy">
          <i class="fa-solid fa-guitar"></i> Akordy
        </a>` : '';

      if (lyricsBtn || chordsBtn) {
        fileDownloadsHtml = `
          <div class="btn-group btn-group-sm" role="group">
            ${lyricsBtn}${chordsBtn}
          </div>`;
      }
    }

    if (!song.pdf_lyrics_path && !song.pdf_chords_path) {
      fileDownloadsHtml = `
        <span class="text-muted small">
          <i class="bi bi-x-circle"></i> Žiadne PDF súbory
        </span>`;
    }

    row.innerHTML = `
      <td class="px-3 py-3">
        <div class="d-flex align-items-center">
          <span class="fw-bold text-primary fs-6">${escapeHtml(song.song_id || '')}</span>
          ${!song.admin_checked ? '<span class="badge bg-warning ms-2 pulse-animation" title="Potrebuje kontrolu"><i class="bi bi-exclamation-triangle"></i></span>' : ''}
        </div>
      </td>

      <td class="px-3 py-3">
        <div class="song-info">
          <div class="fw-bold text-dark mb-1">${escapeHtml(song.title || '')}</div>
          ${song.version_name ? `<div class="text-muted small mb-1"><i class="bi bi-tag me-1"></i>${escapeHtml(song.version_name)}</div>` : ''}
          ${alternativeTitlesHtml}
        </div>
      </td>

      <td class="px-3 py-3">
        <div class="author-info">
          ${song.author ? `<div class="fw-semibold text-dark">${escapeHtml(song.author)}</div>` : ''}
          ${song.author_original && song.author_original !== song.author ?
            `<small class="text-muted"><i class="bi bi-globe me-1"></i>${escapeHtml(song.author_original)}</small>` : ''}
          ${!song.author && !song.author_original ? '<span class="text-muted fst-italic">Neznámy</span>' : ''}
        </div>
      </td>

      <td class="text-center px-3 py-3">
        <div class="audio-files">
          ${mp3Html}
        </div>
      </td>

      <td class="text-center px-3 py-3">
        <div class="d-flex flex-column gap-1 align-items-center">
          <div class="mb-2 d-flex flex-wrap justify-content-center gap-1">
            ${statusBadges.join('')}
          </div>
          ${fileDownloadsHtml}
        </div>
      </td>

      <td class="text-center px-3 py-3">
        <div class="btn-group-vertical btn-group-sm" role="group">
          <a href="/song/${song.id}/view" class="btn btn-outline-info btn-sm mb-1">
            <i class="bi bi-eye me-1"></i>Detaily
          </a>

          <a href="/song/${song.id}" class="btn btn-outline-primary btn-sm mb-1">
            <i class="bi bi-pencil-square me-1"></i>Upraviť
          </a>

          <div class="btn-group btn-group-sm" role="group">
            <form method="POST" action="/song/${song.id}/generate_tex#song-${song.id}" class="d-inline">
              <button type="submit" class="btn btn-outline-warning btn-sm ${song.admin_checked ? 'disabled' : ''}"
                      ${song.admin_checked ? 'title="Admin already checked this song"' : ''}>
                <i class="bi bi-file-code"></i>TeX
              </button>
            </form>

            <form method="GET" action="/generate_pdfs/${song.id}#song-${song.id}" class="d-inline">
              <button type="submit" class="btn btn-primary btn-sm ${!song.tex_path || song.admin_checked ? 'disabled' : ''}"
                      style="background-color: #9b59b6; border-color: #9b59b6;"
                      ${!song.tex_path ? 'title="First generate TeX files"' : ''}
                      ${song.admin_checked ? 'title="Admin already checked this song"' : ''}>
                <i class="bi bi-file-pdf"></i>PDF
              </button>
            </form>
          </div>
        </div>
      </td>
    `;

    tableBody.appendChild(row);
  }

  // Render mobile card
  function renderMobileSong(song) {
    const card = document.createElement('div');
    card.className = 'mobile-song-card song-row';
    card.id = `mobile-song-${song.id}`;
    card.setAttribute('data-categories', (song.categories || '').toLowerCase());
    card.setAttribute('data-printed', song.printed ? 'true' : 'false');
    card.setAttribute('data-checked', song.admin_checked ? 'true' : 'false');
    card.setAttribute('data-search-content', song.title ? song.title.toLowerCase() : '');

    const mp3Count = song.mp3_paths ? song.mp3_paths.length : 0;
    const audioIcon = mp3Count > 0 ?
      `<span class="status-icon status-has-audio" data-bs-toggle="tooltip" data-bs-placement="top" title="${mp3Count} MP3 súborov">
        <i class="bi bi-volume-up-fill"></i>
      </span>` :
      `<span class="status-icon status-no-audio" data-bs-toggle="tooltip" data-bs-placement="top" title="Žiadne MP3">
        <i class="bi bi-volume-mute"></i>
      </span>`;

    // Build categories for the expanded section
    let categoriesHtml = '';
    if (song.categories) {
      const categories = song.categories.split(';;').filter(c => c.trim());
      if (categories.length > 0) {
        categoriesHtml = `
          <div class="mobile-status-badges">
            <small class="text-muted">Kategórie:</small>
            ${categories.map(cat => `<span class="badge bg-light text-dark">${escapeHtml(cat.trim())}</span>`).join('')}
          </div>
        `;
      }
    }

    // Build MP3 buttons/dropdowns for actions grid
    let mp3Button = '';
    if (mp3Count > 0) {
      const encodedMp3Paths = encodeURIComponent(JSON.stringify(song.mp3_paths));
      mp3Button = `
        <button type="button"
                class="btn btn-success btn-sm js-open-file-modal"
                title="Zobraziť MP3 súbory"
                data-file-kind="mp3"
                data-song-id="${song.id}"
                data-song-title="${escapeHtml(song.title || '')}"
                data-file-paths="${encodedMp3Paths}">
          <i class="bi bi-volume-up-fill"></i>
        </button>
      `;
    } else {
      mp3Button = `
        <button type="button" class="btn btn-outline-secondary btn-sm" disabled title="Žiadne MP3 súbory">
          <i class="bi bi-volume-mute"></i>
        </button>
      `;
    }

    // Build sheet music button
    let sheetButton = '';
    if (song.sheet_pdf_paths && song.sheet_pdf_paths.length > 0) {
      const encodedSheetPaths = encodeURIComponent(JSON.stringify(song.sheet_pdf_paths));
      sheetButton = `
        <button type="button"
                class="btn btn-outline-danger btn-sm js-open-file-modal"
                title="Zobraziť notové súbory"
                data-file-kind="sheet"
                data-song-id="${song.id}"
                data-song-title="${escapeHtml(song.title || '')}"
                data-file-paths="${encodedSheetPaths}">
          <i class="fa-solid fa-music"></i>
        </button>
      `;
    } else {
      sheetButton = `
        <button type="button" class="btn btn-outline-secondary btn-sm" disabled title="Žiadne noty">
          <i class="fa-solid fa-music"></i>
        </button>
      `;
    }

    card.innerHTML = `
      <!-- Main Song Info -->
      <div class="mobile-song-header">
        <div class="mobile-song-info">
          <div class="d-flex align-items-start gap-3 mb-2">
            <!-- Status badges on the left, stacked vertically -->
            <div class="mobile-status-badges-left d-flex flex-column gap-1">
              ${song.printed ?
                '<span class="status-icon status-printed" data-bs-toggle="tooltip" data-bs-placement="top" data-bs-original-title="Vytlačené"><i class="bi bi-printer-fill"></i></span>' : ''
              }
              ${song.admin_checked ?
                '<span class="status-icon status-checked" data-bs-toggle="tooltip" data-bs-placement="top" data-bs-original-title="Skontrolované administrátorom"><i class="bi bi-check-circle-fill"></i></span>' : ''
              }
            </div>

            <!-- Song ID and Title side by side -->
            <div class="flex-grow-1">
              <div class="d-flex align-items-start gap-2 mb-1">
                <span class="mobile-song-id">${escapeHtml(song.song_id || '')}</span>
                <div class="mobile-song-title">${escapeHtml(song.title || '')}</div>
              </div>
            </div>
          </div>

          ${song.author ? `<div class="mobile-song-author text-secondary mb-1"><i class="bi bi-person me-1"></i>${escapeHtml(song.author)}</div>` : ''}
          ${song.version_name ? `<div class="text-muted small"><i class="bi bi-tag me-1"></i>${escapeHtml(song.version_name)}</div>` : ''}
        </div>
      </div>

      <!-- Essential Actions (Always Visible) - 4 evenly spaced buttons -->
      <div class="mobile-song-actions-grid">
        <!-- 1. Slová (Lyrics) -->
        ${song.pdf_lyrics_path ?
          `<a href="/uploads/${song.pdf_lyrics_path}" class="btn btn-outline-primary btn-sm" title="Stiahnuť slová" target="_blank">
            <i class="bi bi-file-text"></i>
          </a>` :
          `<button type="button" class="btn btn-outline-secondary btn-sm" disabled title="Žiadne slová">
            <i class="bi bi-file-text"></i>
          </button>`
        }

        <!-- 2. Akordy (Chords) -->
        ${song.pdf_chords_path ?
          `<a href="/uploads/${song.pdf_chords_path}" class="btn btn-outline-warning btn-sm" title="Stiahnuť akordy" target="_blank">
            <i class="fa-solid fa-guitar"></i>
          </a>` :
          `<button type="button" class="btn btn-outline-secondary btn-sm" disabled title="Žiadne akordy">
            <i class="fa-solid fa-guitar"></i>
          </button>`
        }

        <!-- 3. Noty (Sheet Music) -->
        ${sheetButton}

        <!-- 4. MP3 -->
        ${mp3Button}
      </div>

      <!-- Expandable Section for Additional Functionality -->
      <div class="mobile-expandable">
        <button class="mobile-expand-toggle" type="button" data-bs-toggle="collapse" data-bs-target="#mobile-collapse-${song.id}" aria-expanded="false">
          <i class="bi bi-chevron-down"></i>
          Viac možností
        </button>

        <div class="collapse mobile-expanded-content" id="mobile-collapse-${song.id}">
          <!-- Action Buttons -->
          <div class="mobile-files-grid">
            <!-- View (icon only) -->
            <a href="/song/${song.id}/view" class="btn btn-outline-info btn-sm icon-only" title="Detaily">
              <i class="bi bi-eye"></i>
            </a>

            <!-- Edit (icon only) -->
            <a href="/song/${song.id}" class="btn btn-outline-primary btn-sm icon-only" title="Upraviť">
              <i class="bi bi-pencil-square"></i>
            </a>

            <!-- Generate TeX Button (compact) -->
            <form method="POST" action="/song/${song.id}/generate_tex#mobile-song-${song.id}" class="d-inline">
              <button type="submit" class="btn btn-outline-warning btn-sm compact ${song.admin_checked ? 'disabled' : ''}" ${song.admin_checked ? 'title="Admin already checked this song"' : 'title="Generovať TeX súbor"'}>
                <i class="bi bi-file-code"></i>TeX
              </button>
            </form>

            <!-- Generate PDF Button (compact) -->
            <form method="GET" action="/generate_pdfs/${song.id}#mobile-song-${song.id}" class="d-inline">
              <button type="submit" class="btn btn-primary btn-sm compact ${!song.tex_path || song.admin_checked ? 'disabled' : ''}" style="background-color: #9b59b6; border-color: #9b59b6;" ${!song.tex_path ? 'title="First generate TeX files"' : song.admin_checked ? 'title="Admin already checked this song"' : 'title="Generovať PDF súbor"'}>
                <i class="bi bi-file-pdf"></i>PDF
              </button>
            </form>
          </div>

          <!-- Categories -->
          ${categoriesHtml}
        </div>
      </div>
    `;

    mobileContainer.appendChild(card);
  }

  // Show loading indicators
  function showLoadingIndicators() {
    if (tableLoadingIndicator) tableLoadingIndicator.style.display = 'block';
    if (mobileLoadingIndicator) mobileLoadingIndicator.style.display = 'block';
  }

  // Hide loading indicators
  function hideLoadingIndicators() {
    if (tableLoadingIndicator) tableLoadingIndicator.style.display = 'none';
    if (mobileLoadingIndicator) mobileLoadingIndicator.style.display = 'none';
  }

  // Update loaded count display
  function updateLoadedCount() {
    if (loadedSongsCount) loadedSongsCount.textContent = currentlyLoadedSongs;
    if (totalSongsCount) {
      // Show filtered count if we have active filters, otherwise show total
      const displayTotal = activeFilters.size > 0 ? totalFilteredSongs : totalSongs;
      totalSongsCount.textContent = displayTotal;
    }
    if (allSongsLoaded) {
      allSongsLoaded.style.display = allLoaded ? 'inline' : 'none';
      if (allLoaded && activeFilters.size > 0) {
        allSongsLoaded.textContent = ' • Všetky filtrované piesne načítané ✓';
      } else if (allLoaded) {
        allSongsLoaded.textContent = ' • Všetky piesne načítané ✓';
      }
    }
  }

  // Throttle function for scroll events
  function throttle(func, wait) {
    let timeout;
    return function executedFunction(...args) {
      const later = () => {
        clearTimeout(timeout);
        func.apply(this, args);
      };
      clearTimeout(timeout);
      timeout = setTimeout(later, wait);
    };
  }

  // Helper function to escape HTML
  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
  }
}