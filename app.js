class VerszApp {
    constructor() {
        this.currentTrackInterval = null;
        this.recentTracksInterval = null;
        this.searchDebounceTimeout = null;
        
        // Handle GitHub Pages redirect
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
    }

    setupEventListeners() {
        // Auth related events
        document.getElementById('login-btn')?.addEventListener('click', () => this.login());
        document.getElementById('logout-btn')?.addEventListener('click', () => this.logout());
        
        // Navigation events
        window.addEventListener('popstate', () => this.handleRouting());
        
        // Prevent form submission
        document.querySelectorAll('form').forEach(form => 
            form.addEventListener('submit', (e) => e.preventDefault())
        );

        // Click outside search results to close
        document.addEventListener('click', (e) => {
            if (!e.target.closest('#search-container')) {
                this.hideSearchResults();
            }
        });

        // Error message auto-dismiss
        document.querySelectorAll('.error-message').forEach(error => {
            error.addEventListener('click', () => error.classList.add('hidden'));
        });
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
            const response = await fetch(`${config.backendUrl}/users/search?query=${encodeURIComponent(query)}`);
            if (!response.ok) throw new Error('Search failed');
            
            const users = await response.json();
            
            if (users.length === 0) {
                searchResults.innerHTML = `
                    <div class="search-result-item">
                        <div class="placeholder-text">No users found</div>
                    </div>
                `;
            } else {
                searchResults.innerHTML = users.map(user => `
                    <div class="search-result-item" data-userid="${user.id}">
                        <img src="${user.avatar_url || '/api/placeholder/32/32'}" alt="Avatar" class="search-avatar">
                        <div class="search-user-info">
                            <div class="search-username">${user.display_name || user.id}</div>
                            ${user.currently_playing ? `
                                <div class="search-now-playing">
                                    <i class="fas fa-music"></i> ${user.currently_playing}
                                </div>
                            ` : ''}
                        </div>
                    </div>
                `).join('');
            }
            
            searchResults.classList.remove('hidden');
        } catch (error) {
            console.error('Search failed:', error);
            this.showError('Failed to search users. Please try again.');
        }
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

    navigateToProfile(userId) {
        const newPath = `/${userId}`;
        if (window.location.pathname !== newPath) {
            history.pushState({}, '', newPath);
            this.handleRouting();
        }
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
    }

    updateUserInfo(userData) {
        document.getElementById('username').textContent = userData.display_name || userData.id;
        document.getElementById('user-avatar').src = userData.avatar_url || '/api/placeholder/32/32';
        document.getElementById('profile-link').href = `/${userData.id}`;
    }

    updateProfileInfo(userData) {
        document.getElementById('profile-username').textContent = userData.display_name || userData.id;
        document.getElementById('profile-avatar').src = userData.avatar_url || '/api/placeholder/96/96';
        document.title = `${userData.display_name || userData.id} - versz`;
    }

    async startTracking(userId) {
        this.clearIntervals();
        
        await Promise.all([
            this.updateCurrentTrack(userId),
            this.updateRecentTracks(userId)
        ]);
        
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
                            <div class="track-album">${data.album_name}</div>
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
        
        document.getElementById('error-container').appendChild(errorContainer);
        
        setTimeout(() => {
            errorContainer.classList.add('animate__fadeOut');
            setTimeout(() => errorContainer.remove(), 300);
        }, 5000);
    }
}

// Initialize the app when the DOM is fully loaded
document.addEventListener('DOMContentLoaded', () => {
    new VerszApp();
});
