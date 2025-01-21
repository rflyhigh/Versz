function DashboardView() {
    return `
        <div class="dashboard">
            <div class="dashboard-header">
                <h1>Your Lyrics</h1>
                <button class="btn" id="createNew">Create New</button>
            </div>
            <div class="lyrics-grid" id="lyricsGrid"></div>
        </div>
    `;
}
