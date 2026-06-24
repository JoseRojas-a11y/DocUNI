// ============================================================
// DocUNI v3.0 - Controlador del Lado del Cliente
// ============================================================
// Implementa:
// - Comunicación asíncrona con /api/search
// - Renderizado de tarjetas con snippets KWIC
// - Paginación dinámica
// - Deep links con #page=N
// ============================================================

// --- Global State Variables ---
let currentQuery = "";
let currentPage = 1;

// --- Initialize Event Listeners ---
document.addEventListener("DOMContentLoaded", () => {
    // Initialize Lucide Icons for initial page layout
    lucide.createIcons();

    const searchForm = document.getElementById("search-form");
    const searchInput = document.getElementById("search-input");
    const clearButton = document.getElementById("clear-button");
    const prevButton = document.getElementById("prev-btn");
    const nextButton = document.getElementById("next-btn");

    // Search Form Submit
    searchForm.addEventListener("submit", (e) => {
        e.preventDefault();
        const query = searchInput.value.trim();
        if (query) {
            currentQuery = query;
            fetchResults(currentQuery, 1);
        }
    });

    // Search Input text watcher (to show/hide clear button)
    searchInput.addEventListener("input", () => {
        if (searchInput.value.length > 0) {
            clearButton.style.display = "flex";
        } else {
            clearButton.style.display = "none";
        }
    });

    // Clear Button Action
    clearButton.addEventListener("click", () => {
        searchInput.value = "";
        clearButton.style.display = "none";
        searchInput.focus();
        resetSearchState();
    });

    // Previous Page Button
    prevButton.addEventListener("click", () => {
        if (currentPage > 1) {
            fetchResults(currentQuery, currentPage - 1);
        }
    });

    // Next Page Button
    nextButton.addEventListener("click", () => {
        fetchResults(currentQuery, currentPage + 1);
    });
});

// --- Reset UI to Initial State ---
function resetSearchState() {
    currentQuery = "";
    currentPage = 1;
    
    document.getElementById("stats-bar").style.display = "none";
    document.getElementById("results-container").style.display = "none";
    document.getElementById("pagination-section").style.display = "none";
    document.getElementById("no-results-state").style.display = "none";
    document.getElementById("loading-state").style.display = "none";
    
    document.getElementById("empty-state").style.display = "flex";
}

// --- Fetch Search Results from API ---
async function fetchResults(query, page) {
    const loadingState = document.getElementById("loading-state");
    const emptyState = document.getElementById("empty-state");
    const noResultsState = document.getElementById("no-results-state");
    const resultsContainer = document.getElementById("results-container");
    const statsBar = document.getElementById("stats-bar");
    const paginationSection = document.getElementById("pagination-section");

    // Update state to Loading
    loadingState.style.display = "flex";
    emptyState.style.display = "none";
    noResultsState.style.display = "none";
    resultsContainer.style.display = "none";
    statsBar.style.display = "none";
    paginationSection.style.display = "none";

    try {
        const url = `/api/search?q=${encodeURIComponent(query)}&page=${page}`;
        const response = await fetch(url);
        
        if (!response.ok) {
            throw new Error(`Error HTTP: ${response.status}`);
        }
        
        const data = await response.json();
        
        if (data.error) {
            throw new Error(data.error);
        }

        currentPage = data.page;
        displayResults(data);

    } catch (error) {
        console.error("Error al buscar:", error);
        loadingState.style.display = "none";
        
        // Show error alert
        alert("Ocurrió un error al procesar la búsqueda. Por favor verifica la conexión.");
        resetSearchState();
    }
}

// --- Display Results & Update UI ---
function displayResults(data) {
    const loadingState = document.getElementById("loading-state");
    const noResultsState = document.getElementById("no-results-state");
    const resultsContainer = document.getElementById("results-container");
    const statsBar = document.getElementById("stats-bar");
    
    loadingState.style.display = "none";

    // 1. Check if results are empty
    if (!data.results || data.results.length === 0) {
        noResultsState.style.display = "flex";
        return;
    }

    // 2. Render Search Statistics
    const statsText = document.getElementById("search-stats-text");
    statsText.innerHTML = `Se encontraron <strong>${data.total_results}</strong> resultado${data.total_results !== 1 ? 's' : ''} en <strong>${data.time_taken}</strong> s &bull; Pág. <strong>${data.page}</strong> de <strong>${data.pages}</strong>`;
    
    // Render search word tags
    const tagsContainer = document.getElementById("search-terms-tags");
    tagsContainer.innerHTML = "";
    if (data.query_words && data.query_words.length > 0) {
        data.query_words.forEach(word => {
            const badge = document.createElement("span");
            badge.className = "search-tag-badge";
            badge.textContent = word;
            tagsContainer.appendChild(badge);
        });
    }
    statsBar.style.display = "flex";

    // 3. Render Result Cards
    resultsContainer.innerHTML = "";
    data.results.forEach((doc, index) => {
        const docCard = document.createElement("div");
        docCard.className = "doc-card glass-panel";
        // Apply staggering animation delays for smoother entrance
        docCard.style.animationDelay = `${index * 0.04}s`;

        // Determine badge characteristics
        const matchClass = doc.match_type === "all" ? "all" : "some";
        const matchLabel = doc.match_type === "all" ? "Coincidencia Total" : "Coincidencia Parcial";
        const matchIcon = doc.match_type === "all" ? "check-circle" : "check";

        // Format the nombre_compuesto for display (replace underscores with readable path)
        const displayTitle = formatDocumentTitle(doc.nombre_compuesto);

        // Build HTML content for the card
        docCard.innerHTML = `
            <div class="doc-card-header">
                <div class="doc-title-wrapper">
                    <h3 class="doc-title">${escapeHTML(displayTitle)}</h3>
                    <div class="doc-subtitle">${escapeHTML(doc.nombre_compuesto)}</div>
                </div>
                <span class="match-badge ${matchClass}">
                    <i data-lucide="${matchIcon}" style="width: 14px; height: 14px;"></i>
                    <span>${matchLabel}</span>
                </span>
            </div>
            
            <div class="doc-snippet-section">
                <div class="snippet-label">
                    <i data-lucide="file-text" style="width: 14px; height: 14px;"></i>
                    <span>Línea ${doc.numero_linea}</span>
                </div>
                <p class="doc-snippet">${doc.snippet || '<em>Sin vista previa disponible</em>'}</p>
            </div>

            <div class="doc-details-row">
                <div class="detail-item" title="Score BM25 (con bonificación de proximidad)">
                    <i data-lucide="activity"></i>
                    <span>Score BM25: <strong>${doc.bm25_score}</strong></span>
                </div>
                <div class="detail-item" title="Número de línea en el documento">
                    <i data-lucide="bookmark"></i>
                    <span>Línea <strong>${doc.numero_linea}</strong></span>
                </div>
            </div>
            
            <div class="doc-card-footer">
                <div class="matched-info">
                    <span>Términos encontrados:</span>
                    <div class="matched-words-container">
                        ${doc.matched_words.map(w => `<span class="matched-word-token">${escapeHTML(w)}</span>`).join('')}
                    </div>
                </div>
                <a href="${escapeHTML(doc.url_destino)}" target="_blank" rel="noopener noreferrer" class="access-link" id="result-link-${index}">
                    <span>Ver en Drive</span>
                    <i data-lucide="external-link"></i>
                </a>
            </div>
        `;
        
        resultsContainer.appendChild(docCard);
    });

    resultsContainer.style.display = "grid";

    // 4. Render Pagination Controls
    renderPagination(data.page, data.pages);

    // Initialize newly injected Lucide Icons inside the cards
    lucide.createIcons();
    
    // Scroll smoothly to top of search area on page change
    document.querySelector(".search-section").scrollIntoView({ behavior: "smooth", block: "start" });
}

// --- Format Document Title ---
function formatDocumentTitle(nombreCompuesto) {
    if (!nombreCompuesto) return "Documento sin título";
    
    // Extract the last component (actual filename) from the compound name
    const parts = nombreCompuesto.split("_");
    
    // Try to find the actual filename (last meaningful segment)
    // Replace underscores with spaces for readability
    return nombreCompuesto.replace(/_/g, " ");
}

// --- Render Pagination Buttons Helper ---
function renderPagination(currentPage, totalPages) {
    const pageNumbersContainer = document.getElementById("page-numbers");
    pageNumbersContainer.innerHTML = "";
    
    if (totalPages <= 1) {
        document.getElementById("pagination-section").style.display = "none";
        return;
    }
    document.getElementById("pagination-section").style.display = "flex";
    
    // Enable/disable navigation buttons
    document.getElementById("prev-btn").disabled = (currentPage === 1);
    document.getElementById("next-btn").disabled = (currentPage === totalPages);
    
    let pages = [];
    const maxVisible = 5; // How many numbered pages to show at most
    
    if (totalPages <= maxVisible) {
        for (let i = 1; i <= totalPages; i++) {
            pages.push(i);
        }
    } else {
        // Always display first page
        pages.push(1);
        
        let start = Math.max(2, currentPage - 1);
        let end = Math.min(totalPages - 1, currentPage + 1);
        
        if (currentPage <= 3) {
            end = 4;
        }
        if (currentPage >= totalPages - 2) {
            start = totalPages - 3;
        }
        
        // Show left ellipsis if needed
        if (start > 2) {
            pages.push("...");
        }
        
        // Render middle pages
        for (let i = start; i <= end; i++) {
            pages.push(i);
        }
        
        // Show right ellipsis if needed
        if (end < totalPages - 1) {
            pages.push("...");
        }
        
        // Always display last page
        pages.push(totalPages);
    }
    
    // Create elements
    pages.forEach(p => {
        if (p === "...") {
            const span = document.createElement("span");
            span.className = "page-num-dots";
            span.textContent = "...";
            pageNumbersContainer.appendChild(span);
        } else {
            const btn = document.createElement("button");
            btn.className = `page-num-btn ${p === currentPage ? "active" : ""}`;
            btn.textContent = p;
            btn.type = "button";
            btn.id = `page-btn-${p}`;
            if (p !== currentPage) {
                btn.addEventListener("click", () => {
                    fetchResults(currentQuery, p);
                });
            }
            pageNumbersContainer.appendChild(btn);
        }
    });
}

// --- Helper to escape HTML and prevent XSS ---
function escapeHTML(str) {
    if (!str) return "";
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
