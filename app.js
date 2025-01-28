class VerszApp {
    constructor() {
        this.currentTrackInterval = null;
        this.recentTracksInterval = null;
        this.searchDebounceTimeout = null;
        this.dataCache = {
            recentTracks: [],
            topTracks: [],
            topArtists: []
        };
        
        const redirectPath = sessionStorage.getItem('redirect_path');
        if (redirectPath) {
            sessionStorage.removeItem('redirect_path');
            history.replaceState(null, '', redirectPath);
        }
        
        this.setupEventListeners();
        this.checkAuthCallback();
        this.checkExistingSession();
        this.setupSearch();
        this.handleRouting();
        this.setupProfileUrlEditor();
    }

    setupEventListeners() {
        document.getElementById('login-btn')?.addEventListener('click', () => this.login());
        document.getElementById('logout-btn')?.addEventListener('click', () => this.logout());
        window.addEventListener('popstate', () => this.handleRouting());
        
        document.querySelectorAll('form').forEach(form => 
            form.addEventListener('submit', (e) => e.preventDefault())
        );

        document.addEventListener('click', (e) => {
            if (!e.target.closest('#search-container')) {
                this.hideSearchResults();
            }
        });

        // Add tab switching functionality
        const tabs = document.querySelectorAll('.tab-button');
        tabs.forEach(tab => {
            tab.addEventListener('click', (e) => {
                const targetId = e.target.dataset.target;
                this.switchTab(targetId);
            });
        });

        document.querySelectorAll('.error-message').forEach(error => {
            error.addEventListener('click', () => error.classList.add('hidden'));
        });
    }

    switchTab(targetId) {
        // Hide all tab contents
        document.querySelectorAll('.tab-content').forEach(content => {
            content.classList.add('hidden');
        });
        
        // Deactivate all tab buttons
        document.querySelectorAll('.tab-button').forEach(button => {
            button.classList.remove('active');
        });
        
        // Show selected tab content and activate button
        document.getElementById(targetId)?.classList.remove('hidden');
        document.querySelector(`[data-target="${targetId}"]`)?.classList.add('active');
    }

    setupSearch() {
        const searchInput = document.getElementById('user-search');
        const searchResults = document.getElementById('search-results');

        if (!searchInput || !searchResults) return;

        searchInput.addEventListener('input', (e) => {
            clearTimeout(this.searchDebounceTimeout);
            const query = e.target.value.trim();

            if (query.length < 2) {
                this.hideSearchResults();
                return;
            }

            this.searchDebounceTimeout = setTimeout(() => this.performSearch(query), 300);
        });

        searchInput.addEventListener('focus', (e) => {
            if (e.target.value.trim().length >= 2) {
                searchResults.classList.remove('hidden');
            }
        });

        searchResults.addEventListener('click', (e) => {
            const userItem = e.target.closest('.search-result-item');
            if (userItem) {
                const userId = userItem.dataset.userid;
                this.navigateToProfile(userId);
                this.hideSearchResults();
                searchInput.value = '';
                searchInput.blur();
            }
        });
    }

    hideSearchResults() {
        document.getElementById('search-results')?.classList.add('hidden');
    }

    async performSearch(query) {
        const searchResults = document.getElementById('search-results');
        if (!searchResults) return;
    
        try {
            if (!query.trim()) {
                this.hideSearchResults();
                return;
            }
    
            const response = await fetch(`${config.backendUrl}/users/search?query=${encodeURIComponent(query.trim())}`);
            if (!response.ok) throw new Error(`Search failed: ${response.status}`);
            
            const users = await response.json();
            
            searchResults.classList.remove('hidden');
            
            if (users.length === 0) {
                searchResults.innerHTML = `
                    <div class="search-result-item">
                        <div class="placeholder-text">No users found</div>
                    </div>
                `;
            } else {
                searchResults.innerHTML = users.map(user => `
                    <div class="search-result-item" data-userid="${user.id}">
                        <img src="${user.avatar_url || '/api/placeholder/32/32'}" 
                             alt="Avatar" 
                             class="search-avatar"
                             onerror="this.src='/api/placeholder/32/32'">
                        <div class="search-user-info">
                            <div class="search-username">${this.escapeHtml(user.display_name || user.id)}</div>
                        </div>
                    </div>
                `).join('');
            }
        } catch (error) {
            console.error('Search failed:', error);
            searchResults.innerHTML = `
                <div class="search-result-item">
                    <div class="placeholder-text">Search failed. Please try again.</div>
                </div>
            `;
            searchResults.classList.remove('hidden');
        }
    }
    
    escapeHtml(unsafe) {
        return unsafe
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    login() {
        const state = Math.random().toString(36).substring(7);
        localStorage.setItem('spotify_auth_state', state);
        
        const redirectUri = `${window.location.origin}/callback.html`;
        const authUrl = `https://accounts.spotify.com/authorize?client_id=${config.clientId}&response_type=code&redirect_uri=${encodeURIComponent(redirectUri)}&scope=${encodeURIComponent(config.scopes)}&state=${state}`;
        
        localStorage.setItem('login_pending', 'true');
        window.location.href = authUrl;
    }

    logout() {
        localStorage.removeItem('spotify_user_id');
        localStorage.removeItem('login_pending');
        localStorage.removeItem('spotify_auth_state');
        this.clearIntervals();
        window.location.href = '/';
    }

    clearIntervals() {
        if (this.currentTrackInterval) {
            clearInterval(this.currentTrackInterval);
            this.currentTrackInterval = null;
        }
        if (this.recentTracksInterval) {
            clearInterval(this.recentTracksInterval);
            this.recentTracksInterval = null;
        }
    }

    async checkAuthCallback() {
        const urlParams = new URLSearchParams(window.location.search);
        const code = urlParams.get('code');
        const state = urlParams.get('state');
        const storedState = localStorage.getItem('spotify_auth_state');
        
        if (!code) return;

        if (state !== storedState) {
            console.error('State mismatch');
            this.showError('Authentication failed. Please try again.');
            this.logout();
            return;
        }
        
        try {
            const response = await fetch(`${config.backendUrl}/auth/callback`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    code: code,
                    redirect_uri: `${window.location.origin}/callback.html`
                })
            });
            
            if (!response.ok) throw new Error('Authentication failed');
            
            const data = await response.json();
            if (data.success) {
                localStorage.setItem('spotify_user_id', data.user_id);
                localStorage.removeItem('login_pending');
                localStorage.removeItem('spotify_auth_state');
                window.location.href = '/';
            }
        } catch (error) {
            console.error('Authentication failed:', error);
            this.showError('Authentication failed. Please try again.');
            localStorage.removeItem('login_pending');
            localStorage.removeItem('spotify_auth_state');
        }
    }

    async checkExistingSession() {
        const userId = localStorage.getItem('spotify_user_id');
        const loginPending = localStorage.getItem('login_pending');

        if (!userId || loginPending) {
            this.showLoginSection();
            return;
        }

        try {
            const response = await fetch(`${config.backendUrl}/users/${userId}`);
            if (response.ok) {
                await this.handleRouting();
                return;
            }
            throw new Error('Session invalid');
        } catch (error) {
            console.error('Session check failed:', error);
            this.logout();
        }
    }

    setupProfileUrlEditor() {
        const editUrlBtn = document.getElementById('edit-url-btn');
        const urlEditor = document.getElementById('url-editor');
        const urlInput = document.getElementById('custom-url-input');
        const saveUrlBtn = document.getElementById('save-url-btn');
        const cancelUrlBtn = document.getElementById('cancel-url-btn');
        
        if (!editUrlBtn || !urlEditor) return;
        
        editUrlBtn.addEventListener('click', () => {
            urlEditor.classList.remove('hidden');
            editUrlBtn.classList.add('hidden');
        });
        
        cancelUrlBtn.addEventListener('click', () => {
            urlEditor.classList.add('hidden');
            editUrlBtn.classList.remove('hidden');
            urlInput.value = '';
            this.clearUrlValidation();
        });
        
        urlInput.addEventListener('input', async (e) => {
            const value = e.target.value.trim();
            if (value.length < 3) {
                this.showUrlValidation('URL must be at least 3 characters', false);
                return;
            }
            
            if (value.length > 30) {
                this.showUrlValidation('URL must be 30 characters or less', false);
                return;
            }
            
            if (!value.match(/^[a-zA-Z0-9]+$/)) {
                this.showUrlValidation('URL can only contain letters and numbers', false);
                return;
            }
            
            try {
                const response = await fetch(`${config.backendUrl}/users/check-url/${value}`);
                const data = await response.json();
                
                if (data.available) {
                    this.showUrlValidation('URL is available', true);
                    saveUrlBtn.disabled = false;
                } else {
                    this.showUrlValidation(data.reason, false);
                    saveUrlBtn.disabled = true;
                }
            } catch (error) {
                this.showUrlValidation('Error checking URL availability', false);
                saveUrlBtn.disabled = true;
            }
        });
        
        saveUrlBtn.addEventListener('click', async () => {
            const newUrl = urlInput.value.trim().toLowerCase();
            const userId = localStorage.getItem('spotify_user_id');
            
            try {
                const response = await fetch(`${config.backendUrl}/users/${userId}/custom-url`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ custom_url: newUrl })
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to update profile URL');
                }
                
                const data = await response.json();
                history.pushState({}, '', `/${data.custom_url}`);
                urlEditor.classList.add('hidden');
                editUrlBtn.classList.remove('hidden');
                this.showSuccess('Profile URL updated successfully');
            } catch (error) {
                this.showError(error.message);
            }
        });
    }
    
    showUrlValidation(message, isValid) {
        const validation = document.getElementById('url-validation');
        if (!validation) return;
        
        validation.textContent = message;
        validation.className = `validation-message ${isValid ? 'valid' : 'invalid'}`;
    }
    
    clearUrlValidation() {
        const validation = document.getElementById('url-validation');
        if (validation) {
            validation.textContent = '';
            validation.className = 'validation-message';
        }
    }

    // Update existing navigateToProfile method
    navigateToProfile(userId) {
        fetch(`${config.backendUrl}/users/${userId}`)
            .then(response => response.json())
            .then(userData => {
                const profileUrl = userData.custom_url || userData.id;
                const newPath = `/${profileUrl}`;
                if (window.location.pathname !== newPath) {
                    history.pushState({}, '', newPath);
                    this.handleRouting();
                }
            })
            .catch(error => {
                console.error('Failed to get user profile:', error);
                this.showError('Failed to navigate to profile');
            });
    }

    async handleRouting() {
        const path = window.location.pathname;
        const viewingUserId = path === '/' 
            ? localStorage.getItem('spotify_user_id')
            : path.split('/').filter(Boolean)[0];
        
        if (!viewingUserId) {
            this.showLoginSection();
            return;
        }

        try {
            const response = await fetch(`${config.backendUrl}/users/${viewingUserId}`);
            if (!response.ok) throw new Error('User not found');
            
            const userData = await response.json();
            const isOwnProfile = viewingUserId === localStorage.getItem('spotify_user_id');
            await this.showProfileSection(userData, isOwnProfile);
        } catch (error) {
            console.error('Failed to load profile:', error);
            this.showError('Failed to load profile. Please try again later.');
            if (!localStorage.getItem('spotify_user_id')) {
                window.location.href = '/';
            }
        }
    }

    showLoginSection() {
        document.getElementById('login-section')?.classList.remove('hidden');
        document.getElementById('profile-section')?.classList.add('hidden');
        document.getElementById('user-info')?.classList.add('hidden');
        this.clearIntervals();
    }

    async showProfileSection(userData, isOwnProfile) {
        document.getElementById('login-section')?.classList.add('hidden');
        document.getElementById('profile-section')?.classList.remove('hidden');
        
        const loggedInUserId = localStorage.getItem('spotify_user_id');
        if (loggedInUserId) {
            const userInfo = document.getElementById('user-info');
            userInfo?.classList.remove('hidden');
            
            if (!isOwnProfile) {
                try {
                    const response = await fetch(`${config.backendUrl}/users/${loggedInUserId}`);
                    if (response.ok) {
                        const loggedInUserData = await response.json();
                        this.updateUserInfo(loggedInUserData);
                    }
                } catch (error) {
                    console.error('Failed to fetch logged-in user data:', error);
                }
            } else {
                this.updateUserInfo(userData);
            }
        }

        this.updateProfileInfo(userData);
        await this.startTracking(userData.id);
        
        // Show Recent Tracks tab by default
        this.switchTab('recent-tracks');
    }

    updateUserInfo(userData) {
        const username = document.getElementById('username');
        const userAvatar = document.getElementById('user-avatar');
        const profileLink = document.getElementById('profile-link');
        
        if (username) username.textContent = userData.display_name || userData.id;
        if (userAvatar) userAvatar.src = userData.avatar_url || '/api/placeholder/32/32';
        if (profileLink) profileLink.href = `/${userData.id}`;
    }

    updateProfileInfo(userData) {
        const profileUsername = document.getElementById('profile-username');
        const profileAvatar = document.getElementById('profile-avatar');
        
        if (profileUsername) profileUsername.textContent = userData.display_name || userData.id;
        if (profileAvatar) profileAvatar.src = userData.avatar_url || '/api/placeholder/96/96';
        document.title = `${userData.display_name || userData.id} - versz`;
    }

    async startTracking(userId) {
        this.clearIntervals();
        
        await Promise.all([
            this.updateCurrentTrack(userId),
            this.updateRecentTracks(userId),
            this.updateTopTracks(userId),
            this.updateTopArtists(userId)
        ]);
        
        this.currentTrackInterval = setInterval(() => this.updateCurrentTrack(userId), 30000);
        this.recentTracksInterval = setInterval(() => this.updateRecentTracks(userId), 60000);
    }

    async updateCurrentTrack(userId) {
        const currentTrackInfo = document.getElementById('current-track-info');
        if (!currentTrackInfo) return;
        
        try {
            const response = await fetch(`${config.backendUrl}/users/${userId}/currently-playing`);
            if (!response.ok) throw new Error('Failed to fetch current track');
            
            const data = await response.json();
            
            if (data.is_playing) {
                currentTrackInfo.innerHTML = `
                    <div class="track-info">
                        <img src="${data.album_art || '/api/placeholder/64/64'}" 
                             alt="Album Art" 
                             class="track-artwork"
                             onerror="this.src='/api/placeholder/64/64'">
                        <div class="track-details">
                            <div class="track-name">${this.escapeHtml(data.track_name)}</div>
                            <div class="track-artist">${this.escapeHtml(data.artist_name)}</div>
                        </div>
                    </div>
                `;
                currentTrackInfo.classList.add('playing');
            } else {
                currentTrackInfo.innerHTML = `
                    <div class="placeholder-text">
                        <i class="fas fa-music"></i>
                        Not playing anything right now
                    </div>
                `;
                currentTrackInfo.classList.remove('playing');
            }
        } catch (error) {
            console.error('Failed to update current track:', error);
            currentTrackInfo.innerHTML = `
                <div class="placeholder-text">
                    <i class="fas fa-exclamation-circle"></i>
                    Unable to fetch current track
                </div>
            `;
        }
    }

    async updateRecentTracks(userId) {
        const tracksList = document.getElementById('tracks-list');
        const tracksCount = document.getElementById('tracks-count');
        
        if (!tracksList || !tracksCount) return;
        
        try {
            const response = await fetch(`${config.backendUrl}/users/${userId}/recent-tracks`);
            if (!response.ok) throw new Error('Failed to fetch recent tracks');
            
            const tracks = await response.json();
            this.dataCache.recentTracks = tracks;
            
            tracksCount.textContent = tracks.length;
            
            tracksList.innerHTML = tracks.map(track => `
                <div class="track-item">
                    <img src="${track.album_art || '/api/placeholder/48/48'}" alt="Album Art" 
                class="track-artwork"
                onerror="this.src='/api/placeholder/48/48'">
                <div class="track-details">
                    <div class="track-name">${this.escapeHtml(track.track_name)}</div>
                    <div class="track-artist">${this.escapeHtml(track.artist_name)}</div>
                    <div class="track-time">${this.formatDate(track.played_at)}</div>
                </div>
            </div>
        `).join('');
        } catch (error) {
            console.error('Failed to update recent tracks:', error);
            tracksList.innerHTML = `
                <div class="placeholder-text">
                    <i class="fas fa-exclamation-circle"></i>
                    Unable to fetch recent tracks
                </div>
            `;
            tracksCount.textContent = '0';
        }
    }

    async updateTopTracks(userId) {
        const topTracksList = document.getElementById('top-tracks-list');
        const topTracksCount = document.getElementById('top-tracks-count');
        
        if (!topTracksList || !topTracksCount) return;
        
        try {
            const response = await fetch(`${config.backendUrl}/users/${userId}/top-tracks`);
            if (!response.ok) throw new Error('Failed to fetch top tracks');
            
            const tracks = await response.json();
            this.dataCache.topTracks = tracks;
            
            topTracksCount.textContent = tracks.length;
            
            topTracksList.innerHTML = tracks.map((track, index) => `
                <div class="track-item">
                    <div class="track-rank">${index + 1}</div>
                    <img src="${track.album_art || '/api/placeholder/48/48'}" 
                         alt="Album Art" 
                         class="track-artwork"
                         onerror="this.src='/api/placeholder/48/48'">
                    <div class="track-details">
                        <div class="track-name">${this.escapeHtml(track.track_name)}</div>
                        <div class="track-artist">${this.escapeHtml(track.artist_name)}</div>
                        <div class="track-popularity">Popularity: ${track.popularity}%</div>
                    </div>
                </div>
            `).join('');
        } catch (error) {
            console.error('Failed to update top tracks:', error);
            topTracksList.innerHTML = `
                <div class="placeholder-text">
                    <i class="fas fa-exclamation-circle"></i>
                    Unable to fetch top tracks
                </div>
            `;
            topTracksCount.textContent = '0';
        }
    }

    async updateTopArtists(userId) {
        const topArtistsList = document.getElementById('top-artists-list');
        const topArtistsCount = document.getElementById('top-artists-count');
        
        if (!topArtistsList || !topArtistsCount) return;
        
        try {
            const response = await fetch(`${config.backendUrl}/users/${userId}/top-artists`);
            if (!response.ok) throw new Error('Failed to fetch top artists');
            
            const artists = await response.json();
            this.dataCache.topArtists = artists;
            
            topArtistsCount.textContent = artists.length;
            
            topArtistsList.innerHTML = artists.map((artist, index) => `
                <div class="artist-item">
                    <div class="artist-rank">${index + 1}</div>
                    <img src="${artist.artist_image || '/api/placeholder/64/64'}" 
                         alt="Artist" 
                         class="artist-artwork"
                         onerror="this.src='/api/placeholder/64/64'">
                    <div class="artist-details">
                        <div class="artist-name">${this.escapeHtml(artist.artist_name)}</div>
                        <div class="artist-popularity">Popularity: ${artist.popularity}%</div>
                    </div>
                </div>
            `).join('');
        } catch (error) {
            console.error('Failed to update top artists:', error);
            topArtistsList.innerHTML = `
                <div class="placeholder-text">
                    <i class="fas fa-exclamation-circle"></i>
                    Unable to fetch top artists
                </div>
            `;
            topArtistsCount.textContent = '0';
        }
    }

    formatDate(dateString) {
        const date = new Date(dateString);
        const now = new Date();
        const diff = Math.floor((now - date) / 1000);
        
        if (diff < 60) return 'Just now';
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
        return date.toLocaleDateString();
    }

    showError(message) {
        const errorContainer = document.createElement('div');
        errorContainer.className = 'error-message animate__animated animate__fadeIn';
        errorContainer.textContent = message;
        
        const container = document.getElementById('error-container');
        if (container) {
            container.appendChild(errorContainer);
            
            setTimeout(() => {
                errorContainer.classList.add('animate__fadeOut');
                setTimeout(() => errorContainer.remove(), 300);
            }, 5000);
        }
    }
}

// Initialize the app when the DOM is fully loaded
document.addEventListener('DOMContentLoaded', () => {
    new VerszApp();
});
