// app.js
class VerszApp {
    constructor() {
        this.setupEventListeners();
        this.checkAuthCallback();
        this.checkExistingSession();
        this.setupSearch();
        this.handleRouting();
    }

    setupEventListeners() {
        document.getElementById('login-btn')?.addEventListener('click', () => this.login());
        document.getElementById('logout-btn')?.addEventListener('click', () => this.logout());
        window.addEventListener('popstate', () => this.handleRouting());
    }

    setupSearch() {
        const searchInput = document.getElementById('user-search');
        const searchResults = document.getElementById('search-results');

        searchInput?.addEventListener('input', async (e) => {
            const query = e.target.value.trim();
            if (query.length < 2) {
                searchResults.classList.add('hidden');
                return;
            }

            try {
                const response = await fetch(`${config.backendUrl}/users/search?query=${encodeURIComponent(query)}`);
                const users = await response.json();
                
                searchResults.innerHTML = users.map(user => `
                    <div class="search-result-item" data-userid="${user.id}">
                        ${user.id}
                    </div>
                `).join('');
                
                searchResults.classList.remove('hidden');
            } catch (error) {
                console.error('Search failed:', error);
            }
        });

        searchResults?.addEventListener('click', (e) => {
            const userItem = e.target.closest('.search-result-item');
            if (userItem) {
                const userId = userItem.dataset.userid;
                window.location.href = `/${userId}`;
            }
        });

        document.addEventListener('click', (e) => {
            if (!e.target.closest('#search-container')) {
                searchResults?.classList.add('hidden');
            }
        });
    }

    login() {
        const redirectUri = `${window.location.origin}/callback.html`;
        const authUrl = `https://accounts.spotify.com/authorize?client_id=${config.clientId}&response_type=code&redirect_uri=${encodeURIComponent(redirectUri)}&scope=${encodeURIComponent(config.scopes)}`;
        window.location.href = authUrl;
    }

    logout() {
        localStorage.removeItem('spotify_user_id');
        window.location.href = '/';
    }

    async checkAuthCallback() {
        const urlParams = new URLSearchParams(window.location.search);
        const code = urlParams.get('code');
        
        if (code) {
            try {
                const response = await fetch(`${config.backendUrl}/auth/callback?code=${code}`);
                const data = await response.json();
                
                if (data.success) {
                    localStorage.setItem('spotify_user_id', data.user_id);
                    window.location.href = '/';
                }
            } catch (error) {
                console.error('Authentication failed:', error);
            }
        }
    }

    async checkExistingSession() {
        const userId = localStorage.getItem('spotify_user_id');
        if (userId) {
            await this.handleRouting();
        } else {
            this.showLoginSection();
        }
    }

    async handleRouting() {
        const path = window.location.pathname;
        const viewingUserId = path === '/' ? localStorage.getItem('spotify_user_id') : path.substring(1);
        
        if (!viewingUserId) {
            this.showLoginSection();
            return;
        }

        const isOwnProfile = viewingUserId === localStorage.getItem('spotify_user_id');
        await this.showProfileSection(viewingUserId, isOwnProfile);
    }

    showLoginSection() {
        document.getElementById('login-section').classList.remove('hidden');
        document.getElementById('profile-section').classList.add('hidden');
        document.getElementById('user-info').classList.add('hidden');
    }

    async showProfileSection(userId, isOwnProfile) {
        document.getElementById('login-section').classList.add('hidden');
        document.getElementById('profile-section').classList.remove('hidden');
        
        if (isOwnProfile) {
            document.getElementById('user-info').classList.remove('hidden');
            document.getElementById('username').textContent = userId;
            document.getElementById('profile-link').href = `/${userId}`;
        } else {
            document.getElementById('user-info').classList.add('hidden');
        }

        document.getElementById('profile-username').textContent = userId;
        
        await this.startTracking(userId);
    }

    async startTracking(userId) {
        await this.updateCurrentTrack(userId);
        await this.updateRecentTracks(userId);
        
        if (this.currentTrackInterval) {
            clearInterval(this.currentTrackInterval);
        }
        
        if (this.recentTracksInterval) {
            clearInterval(this.recentTracksInterval);
        }
        
        this.currentTrackInterval = setInterval(() => this.updateCurrentTrack(userId), 30000);
        this.recentTracksInterval = setInterval(() => this.updateRecentTracks(userId), 120000);
    }

    async updateCurrentTrack(userId) {
        try {
            const response = await fetch(`${config.backendUrl}/users/${userId}/currently-playing`);
            const data = await response.json();
            
            const currentTrackInfo = document.getElementById('current-track-info');
            if (data.is_playing) {
                currentTrackInfo.innerHTML = `
                    <div class="track-name">${data.track_name}</div>
                    <div class="track-artist">${data.artist_name}</div>
                `;
                currentTrackInfo.classList.add('playing');
            } else {
                currentTrackInfo.innerHTML = '<div class="placeholder-text">Not playing anything right now</div>';
                currentTrackInfo.classList.remove('playing');
            }
        } catch (error) {
            console.error('Failed to update current track:', error);
        }
    }

    async updateRecentTracks(userId) {
        try {
            const response = await fetch(`${config.backendUrl}/users/${userId}/recent-tracks`);
            const tracks = await response.json();
            
            document.getElementById('tracks-count').textContent = tracks.length;
            
            const artists = new Set(tracks.map(track => track.artist_name));
            document.getElementById('artists-count').textContent = artists.size;
            
            const tracksList = document.getElementById('tracks-list');
            tracksList.innerHTML = tracks.map(track => `
                <div class="track-item animate__animated animate__fadeIn">
                    <div class="track-name">${track.track_name}</div>
                    <div class="track-artist">${track.artist_name}</div>
                    <div class="track-time">${this.formatDate(track.played_at)}</div>
                </div>
            `).join('');
        } catch (error) {
            console.error('Failed to update recent tracks:', error);
        }
    }

    formatDate(dateString) {
        const date = new Date(dateString);
        const now = new Date();
        const diff = Math.floor((now - date) / 1000);
        
        if (diff < 60) return 'Just now';
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        return date.toLocaleDateString();
    }
}


new VerszApp();
