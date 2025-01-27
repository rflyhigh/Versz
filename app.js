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
                        <img src="${user.avatar_url || '/api/placeholder/32/32'}" alt="Avatar" class="search-avatar">
                        <span class="search-username">${user.display_name || user.id}</span>
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
                this.navigateToProfile(userId);
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
        localStorage.setItem('login_pending', 'true');
        window.location.href = authUrl;
    }

    logout() {
        localStorage.removeItem('spotify_user_id');
        localStorage.removeItem('login_pending');
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
                    localStorage.removeItem('login_pending');
                    window.location.href = '/';
                }
            } catch (error) {
                console.error('Authentication failed:', error);
                localStorage.removeItem('login_pending');
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
            // If we get here, the session is invalid
            this.logout();
        } else {
            this.showLoginSection();
        }
    }

    navigateToProfile(userId) {
        // Update URL without triggering page reload
        history.pushState({}, '', `/${userId}`);
        this.handleRouting();
    }

    async handleRouting() {
        // Remove any query parameters from the path
        const path = window.location.pathname;
        const viewingUserId = path === '/' ? 
            localStorage.getItem('spotify_user_id') : 
            path.split('/').filter(Boolean)[0];  // Get first non-empty segment
        
        if (!viewingUserId) {
            this.showLoginSection();
            return;
        }

        try {
            const response = await fetch(`${config.backendUrl}/users/${viewingUserId}`);
            if (!response.ok) {
                throw new Error('User not found');
            }
            const userData = await response.json();
            
            const isOwnProfile = viewingUserId === localStorage.getItem('spotify_user_id');
            await this.showProfileSection(userData, isOwnProfile);
        } catch (error) {
            console.error('Failed to load user profile:', error);
            window.location.href = '/';
        }
    }

    setupEventListeners() {
        document.getElementById('login-btn')?.addEventListener('click', () => this.login());
        document.getElementById('logout-btn')?.addEventListener('click', () => this.logout());
        
        // Update popstate handler to properly handle browser back/forward
        window.addEventListener('popstate', (event) => {
            this.handleRouting();
        });
    }

    showLoginSection() {
        document.getElementById('login-section').classList.remove('hidden');
        document.getElementById('profile-section').classList.add('hidden');
        document.getElementById('user-info').classList.add('hidden');
    }

    async showProfileSection(userData, isOwnProfile) {
        document.getElementById('login-section').classList.add('hidden');
        document.getElementById('profile-section').classList.remove('hidden');
        
        // Update header user info if logged in
        const loggedInUserId = localStorage.getItem('spotify_user_id');
        if (loggedInUserId) {
            const userInfo = document.getElementById('user-info');
            userInfo.classList.remove('hidden');
            
            // Fetch logged-in user data if viewing another profile
            if (!isOwnProfile) {
                try {
                    const response = await fetch(`${config.backendUrl}/users/${loggedInUserId}`);
                    if (response.ok) {
                        const loggedInUserData = await response.json();
                        document.getElementById('username').textContent = loggedInUserData.display_name || loggedInUserData.id;
                        document.getElementById('user-avatar').src = loggedInUserData.avatar_url || '/api/placeholder/32/32';
                        document.getElementById('profile-link').href = `/${loggedInUserData.id}`;
                    }
                } catch (error) {
                    console.error('Failed to fetch logged-in user data:', error);
                }
            } else {
                // Use current userData for own profile
                document.getElementById('username').textContent = userData.display_name || userData.id;
                document.getElementById('user-avatar').src = userData.avatar_url || '/api/placeholder/32/32';
                document.getElementById('profile-link').href = `/${userData.id}`;
            }
        }

        // Update profile section
        document.getElementById('profile-username').textContent = userData.display_name || userData.id;
        document.getElementById('profile-avatar').src = userData.avatar_url || '/api/placeholder/150/150';
        
        await this.startTracking(userData.id);
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
        return date.toLocaleDateString();
    }
}

new VerszApp();
