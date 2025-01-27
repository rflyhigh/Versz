class VerszApp {
    constructor() {
        this.currentTrackInterval = null;
        this.recentTracksInterval = null;
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
        document.querySelector('form')?.addEventListener('submit', (e) => e.preventDefault());
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
                if (!response.ok) throw new Error('Search failed');
                
                const users = await response.json();
                searchResults.innerHTML = users.map(user => `
                    <div class="search-result-item" data-userid="${user.id}">
                        <img src="${user.avatar_url || '/api/placeholder/32/32'}" alt="Avatar" class="search-avatar">
                        <span class="search-username">${user.display_name || user.id}</span>
                    </div>
                `).join('');
                
                searchResults.classList.remove('hidden');
            } catch (error) {
                console.error('Search failed:', error);
                this.showError('search-error', 'Failed to search users. Please try again.');
            }
        });

        searchResults?.addEventListener('click', (e) => {
            const userItem = e.target.closest('.search-result-item');
            if (userItem) {
                const userId = userItem.dataset.userid;
                this.navigateToProfile(userId);
                searchResults.classList.add('hidden');
                searchInput.value = '';
            }
        });

        document.addEventListener('click', (e) => {
            if (!e.target.closest('#search-container')) {
                searchResults?.classList.add('hidden');
            }
        });
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
        
        if (code) {
            if (state !== storedState) {
                console.error('State mismatch');
                this.logout();
                return;
            }
            
            try {
                const response = await fetch(`${config.backendUrl}/auth/callback`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
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
                localStorage.removeItem('login_pending');
                localStorage.removeItem('spotify_auth_state');
                this.showError('login-error', 'Authentication failed. Please try again.');
            }
        }
    }

    async checkExistingSession() {
        const userId = localStorage.getItem('spotify_user_id');
        const loginPending = localStorage.getItem('login_pending');

        if (userId && !loginPending) {
            try {
                const response = await fetch(`${config.backendUrl}/users/${userId}`);
                if (response.ok) {
                    await this.handleRouting();
                    return;
                }
            } catch (error) {
                console.error('Session check failed:', error);
            }
            this.logout();
        } else {
            this.showLoginSection();
        }
    }

    navigateToProfile(userId) {
        const newPath = `/${userId}`;
        if (window.location.pathname !== newPath) {
            history.pushState({}, '', newPath);
            this.handleRouting();
        }
    }

    async handleRouting() {
        const path = window.location.pathname;
        let viewingUserId;
        
        if (path === '/') {
            viewingUserId = localStorage.getItem('spotify_user_id');
        } else {
            const segments = path.split('/').filter(Boolean);
            viewingUserId = segments[0];
        }
        
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
            console.error('Failed to load user profile:', error);
            this.showError('profile-error', 'Failed to load profile. Please try again later.');
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
    }

    updateUserInfo(userData) {
        document.getElementById('username').textContent = userData.display_name || userData.id;
        document.getElementById('user-avatar').src = userData.avatar_url || '/api/placeholder/32/32';
        document.getElementById('profile-link').href = `/${userData.id}`;
    }

    updateProfileInfo(userData) {
        document.getElementById('profile-username').textContent = userData.display_name || userData.id;
        document.getElementById('profile-avatar').src = userData.avatar_url || '/api/placeholder/150/150';
    }

    async startTracking(userId) {
        this.clearIntervals();
        
        await this.updateCurrentTrack(userId);
        await this.updateRecentTracks(userId);
        
        this.currentTrackInterval = setInterval(() => this.updateCurrentTrack(userId), 30000);
        this.recentTracksInterval = setInterval(() => this.updateRecentTracks(userId), 120000);
    }

    async updateCurrentTrack(userId) {
        try {
            const response = await fetch(`${config.backendUrl}/users/${userId}/currently-playing`);
            if (!response.ok) throw new Error('Failed to fetch current track');
            
            const data = await response.json();
            const currentTrackInfo = document.getElementById('current-track-info');
            
            if (data.is_playing) {
                currentTrackInfo.innerHTML = `
                    <div class="track-info">
                        <img src="${data.album_art || '/api/placeholder/64/64'}" alt="Album Art" class="track-artwork">
                        <div class="track-details">
                            <div class="track-name">${data.track_name}</div>
                            <div class="track-artist">${data.artist_name}</div>
                        </div>
                    </div>
                `;
                currentTrackInfo.classList.add('playing');
            } else {
                currentTrackInfo.innerHTML = '<div class="placeholder-text">Not playing anything right now</div>';
                currentTrackInfo.classList.remove('playing');
            }
        } catch (error) {
            console.error('Failed to update current track:', error);
            this.showError('track-error', 'Failed to update current track.');
        }
    }

    async updateRecentTracks(userId) {
        try {
            const response = await fetch(`${config.backendUrl}/users/${userId}/recent-tracks`);
            if (!response.ok) throw new Error('Failed to fetch recent tracks');
            
            const tracks = await response.json();
            
            document.getElementById('tracks-count').textContent = tracks.length;
            
            const artists = new Set(tracks.map(track => track.artist_name));
            document.getElementById('artists-count').textContent = artists.size;
            
            const tracksList = document.getElementById('tracks-list');
            tracksList.innerHTML = tracks.map(track => `
                <div class="track-item animate__animated animate__fadeIn">
                    <img src="${track.album_art || '/api/placeholder/48/48'}" alt="Album Art" class="track-artwork">
                    <div class="track-details">
                        <div class="track-name">${track.track_name}</div>
                        <div class="track-artist">${track.artist_name}</div>
                        <div class="track-time">${this.formatDate(track.played_at)}</div>
                    </div>
                </div>
            `).join('');
        } catch (error) {
            console.error('Failed to update recent tracks:', error);
            this.showError('recent-tracks-error', 'Failed to update recent tracks.');
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

    showError(elementId, message) {
        const errorElement = document.getElementById(elementId);
        if (errorElement) {
            errorElement.textContent = message;
            errorElement.classList.remove('hidden');
            setTimeout(() => {
                errorElement.classList.add('hidden');
            }, 5000);
        }
    }
}

// Initialize the app when the DOM is fully loaded
document.addEventListener('DOMContentLoaded', () => {
    new VerszApp();
});
