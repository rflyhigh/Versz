class VerszApp {
    constructor() {
        this.intervals = {
            currentTrack: null,
            recentTracks: null,
            topTracks: null,
            topArtists: null,
            playlists: null
        };
        this.searchDebounceTimeout = null;
        this.urlCheckTimeout = null;
        this.dataCache = {
            recentTracks: [],
            topTracks: [],
            topArtists: []
        };

        this.initializeApp();
    }

    async initializeApp() {
        // Remove any duplicate profile sections
        this.cleanupDuplicateSections();
        this.handleRedirectPath();
        this.setupEventListeners();
        await this.checkAuthCallback();
        await this.checkExistingSession();
        this.setupSearch();
        await this.handleRouting();
    }

    cleanupDuplicateSections() {
        // Remove duplicate profile sections if they exist
        const profileSections = document.querySelectorAll('#profile-section');
        if (profileSections.length > 1) {
            for (let i = 1; i < profileSections.length; i++) {
                profileSections[i].remove();
            }
        }
    }

    handleRedirectPath() {
        const redirectPath = sessionStorage.getItem('redirect_path');
        if (redirectPath) {
            sessionStorage.removeItem('redirect_path');
            history.replaceState(null, '', redirectPath);
        }
    }

    setupEventListeners() {
        // Login/Logout events
        this.addClickListener('login-btn', () => this.login());
        this.addClickListener('logout-btn', () => this.logout());
        this.addClickListener('quick-login-btn', () => this.login());
        this.addClickListener('custom-login-btn', () => this.login(true));
        this.addClickListener('custom-url-toggle', () => this.toggleCustomUrlSection());

        // Form prevention
        document.querySelectorAll('form').forEach(form => 
            form.addEventListener('submit', e => e.preventDefault())
        );

        // Global click handler for search
        document.addEventListener('click', e => {
            if (!e.target.closest('#search-container')) {
                this.hideSearchResults();
            }
        });

        // Tab switching
        document.querySelectorAll('.tab-button').forEach(tab => {
            tab.addEventListener('click', e => {
                this.switchTab(e.target.dataset.target);
            });
        });

        // Error message handling
        document.querySelectorAll('.error-message').forEach(error => {
            error.addEventListener('click', () => error.classList.add('hidden'));
        });

        // URL input handling
        const urlInput = document.getElementById('custom-url-input');
        if (urlInput) {
            urlInput.addEventListener('input', e => this.handleUrlInput(e));
        }

        // Navigation
        window.addEventListener('popstate', () => this.handleRouting());
    }

    addClickListener(id, callback) {
        const element = document.getElementById(id);
        if (element) {
            element.addEventListener('click', callback);
        }
    }


    handleUrlInput(e) {
        const urlInput = e.target;
        const urlStatus = document.getElementById('url-status');
        const customLoginBtn = document.getElementById('custom-login-btn');
        
        clearTimeout(this.urlCheckTimeout);
        
        const url = urlInput.value.trim();
        const validationResult = this.validateUrl(url);
        
        if (!validationResult.isValid) {
            this.updateUrlStatus(urlStatus, validationResult.message, 'red');
            customLoginBtn.disabled = true;
            return;
        }
    
        this.updateUrlStatus(urlStatus, 'Checking availability...', 'gray');
        
        this.urlCheckTimeout = setTimeout(async () => {
            try {
                const response = await fetch(`${config.backendUrl}/check-url/${url}`);
                const data = await response.json();
                
                if (data.available) {
                    this.updateUrlStatus(urlStatus, 'URL is available!', 'green');
                    customLoginBtn.disabled = false;
                    urlInput.dataset.valid = 'true';
                } else {
                    this.updateUrlStatus(urlStatus, data.reason || 'URL is already taken', 'red');
                    customLoginBtn.disabled = true;
                    urlInput.dataset.valid = 'false';
                }
            } catch (error) {
                console.error('URL check failed:', error);
                this.updateUrlStatus(urlStatus, 'Error checking URL availability', 'red');
                customLoginBtn.disabled = true;
                urlInput.dataset.valid = 'false';
            }
        }, 300);
    }
    
    validateUrl(url) {
        if (!url) {
            return { isValid: false, message: '' };
        }
        if (url.length < 3) {
            return { isValid: false, message: 'URL must be at least 3 characters' };
        }
        if (!/^[a-zA-Z0-9_-]{3,30}$/.test(url)) {
            return { isValid: false, message: 'URL can only contain letters, numbers, underscores, and hyphens' };
        }
        return { isValid: true, message: '' };
    }
    
    updateUrlStatus(element, message, color) {
        if (element) {
            element.textContent = message;
            element.className = `text-sm text-${color}-500`;
        }
    }

    updateCustomLoginButton(button, enabled) {
        if (button) {
            button.disabled = !enabled;
        }
    }

    toggleCustomUrlSection() {
        const section = document.getElementById('custom-url-section');
        if (!section) return;

        const isHidden = section.classList.contains('hidden');
        
        if (isHidden) {
            section.classList.remove('hidden');
            section.classList.add('animate__fadeIn');
        } else {
            section.classList.add('animate__fadeOut');
            setTimeout(() => {
                section.classList.add('hidden');
                section.classList.remove('animate__fadeOut');
            }, 300);
        }
    }

    setupSearch() {
        const searchInput = document.getElementById('user-search');
        const searchResults = document.getElementById('search-results');

        if (!searchInput || !searchResults) return;

        searchInput.addEventListener('input', this.handleSearchInput.bind(this));
        searchInput.addEventListener('focus', this.handleSearchFocus.bind(this));
        searchResults.addEventListener('click', this.handleSearchResultClick.bind(this));
    }

    handleSearchInput(e) {
        clearTimeout(this.searchDebounceTimeout);
        const query = e.target.value.trim();

        if (query.length < 2) {
            this.hideSearchResults();
            return;
        }

        this.searchDebounceTimeout = setTimeout(() => this.performSearch(query), 300);
    }

    handleSearchFocus(e) {
        if (e.target.value.trim().length >= 2) {
            document.getElementById('search-results')?.classList.remove('hidden');
        }
    }

    handleSearchResultClick(e) {
        const userItem = e.target.closest('.search-result-item');
        if (userItem) {
            const userId = userItem.dataset.userid;
            this.navigateToProfile(userId);
            this.hideSearchResults();
            const searchInput = document.getElementById('user-search');
            if (searchInput) {
                searchInput.value = '';
                searchInput.blur();
            }
        }
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
            this.renderSearchResults(searchResults, users);
        } catch (error) {
            console.error('Search failed:', error);
            this.renderSearchError(searchResults);
        }
    }

    renderSearchResults(container, users) {
        container.classList.remove('hidden');
        
        if (users.length === 0) {
            container.innerHTML = this.createSearchPlaceholder('No users found');
            return;
        }

        container.innerHTML = users.map(user => this.createSearchResultItem(user)).join('');
    }

    renderSearchError(container) {
        container.innerHTML = this.createSearchPlaceholder('Search failed. Please try again.');
        container.classList.remove('hidden');
    }

    createSearchPlaceholder(text) {
        return `
            <div class="search-result-item">
                <div class="placeholder-text">${text}</div>
            </div>
        `;
    }

    createSearchResultItem(user) {
        return `
            <div class="search-result-item" data-userid="${user.id}">
                <img src="${user.avatar_url || 'https://placehold.co/32'}" 
                     alt="Avatar" 
                     class="search-avatar"
                     onerror="this.src='https://placehold.co/32'">
                <div class="search-user-info">
                    <div class="search-username">${this.escapeHtml(user.display_name || user.id)}</div>
                </div>
            </div>
        `;
    }

    hideSearchResults() {
        document.getElementById('search-results')?.classList.add('hidden');
    }

    async login(useCustomUrl = false) {
        let customUrl = '';
        
        if (useCustomUrl) {
            const urlInput = document.getElementById('custom-url-input');
            if (!urlInput || urlInput.dataset.valid !== 'true') {
                this.showError('Please choose a valid URL');
                return;
            }
            customUrl = urlInput.value.trim();
        }

        const state = Math.random().toString(36).substring(7);
        this.setLoginStorage(state, customUrl);
        
        const redirectUri = `${window.location.origin}/callback.html`;
        const authUrl = `https://accounts.spotify.com/authorize?client_id=${config.clientId}&response_type=code&redirect_uri=${encodeURIComponent(redirectUri)}&scope=${encodeURIComponent(config.scopes)}&state=${state}`;
        
        window.location.href = authUrl;
    }

    setLoginStorage(state, customUrl) {
        localStorage.setItem('spotify_auth_state', state);
        localStorage.setItem('login_pending', 'true');
        if (customUrl) {
            localStorage.setItem('pending_custom_url', customUrl);
        }
    }

    logout() {
        this.clearStorage();
        this.clearIntervals();
        window.location.href = '/';
    }

    clearStorage() {
        const keys = ['spotify_user_id', 'login_pending', 'spotify_auth_state', 'pending_custom_url'];
        keys.forEach(key => localStorage.removeItem(key));
    }

    clearIntervals() {
        Object.keys(this.intervals).forEach(key => {
            if (this.intervals[key]) {
                clearInterval(this.intervals[key]);
                this.intervals[key] = null;
            }
        });
    }

    async checkAuthCallback() {
        const urlParams = new URLSearchParams(window.location.search);
        const code = urlParams.get('code');
        const state = urlParams.get('state');
        
        if (!code) return;

        if (!this.validateAuthState(state)) {
            this.handleAuthError('Authentication failed. Please try again.');
            return;
        }

        try {
            await this.processAuthCallback(code);
        } catch (error) {
            this.handleAuthError(error.message);
        }
    }

    validateAuthState(state) {
        const storedState = localStorage.getItem('spotify_auth_state');
        return state === storedState;
    }

    async processAuthCallback(code) {
        const customUrl = localStorage.getItem('pending_custom_url');
        const response = await fetch(`${config.backendUrl}/auth/callback`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                code,
                custom_url: customUrl,
                redirect_uri: `${window.location.origin}/callback.html`
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Authentication failed');
        }

        const data = await response.json();
        if (data.success) {
            this.handleSuccessfulAuth(data.user_id);
        }
    }

    handleSuccessfulAuth(userId) {
        localStorage.setItem('spotify_user_id', userId);
        this.clearAuthStorage();
        window.location.href = '/';
    }

    handleAuthError(message) {
        console.error('Authentication error:', message);
        this.showError(message);
        this.clearAuthStorage();
    }

    clearAuthStorage() {
        const keys = ['login_pending', 'spotify_auth_state', 'pending_custom_url'];
        keys.forEach(key => localStorage.removeItem(key));
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
            if (!response.ok) throw new Error('Session invalid');
            
            await this.handleRouting();
        } catch (error) {
            console.error('Session check failed:', error);
            this.logout();
        }
    }

    async navigateToProfile(userId) {
        if (!userId) {
            console.error('Attempted to navigate to profile with no userId');
            this.showLoginSection();
            return;
        }
    
        const newPath = `/${userId}`;
        if (window.location.pathname !== newPath) {
            history.pushState({}, '', newPath);
            await this.loadProfile(userId);
        }
    }

    async loadProfile(identifier) {
        try {
            const response = await fetch(`${config.backendUrl}/users/${identifier}`);
            if (!response.ok) {
                if (response.status === 404) {
                    throw new Error('User not found');
                }
                throw new Error('Failed to load profile');
            }
            
            const userData = await response.json();
            const isOwnProfile = identifier === localStorage.getItem('spotify_user_id');
            
            // Clear existing content and show profile section
            this.clearMainContent();
            await this.showProfileSection(userData, isOwnProfile);
        } catch (error) {
            console.error('Profile load error:', error);
            throw error;
        }
    }
    async handleRouting() {
        const pathParts = window.location.pathname.split('/').filter(Boolean);
        
        // Clear any existing intervals before loading new content
        this.clearIntervals();
    
        // If no path, show login or profile
        if (pathParts.length === 0) {
            const userId = localStorage.getItem('spotify_user_id');
            if (!userId) {
                this.showLoginSection();
                return;
            }
            await this.navigateToProfile(userId);
            return;
        }
    
        const [identifier, playlistUrl] = pathParts;
    
        // Handle playlist view
        if (playlistUrl) {
            try {
                await this.loadPlaylistView(identifier, playlistUrl);
            } catch (error) {
                console.error('Failed to load playlist:', error);
                this.showError('Failed to load playlist');
                this.showLoginSection();
            }
            return;
        }
    
        // Handle profile view
        try {
            await this.loadProfile(identifier);
        } catch (error) {
            console.error('Profile load error:', error);
            if (error.message === 'User not found') {
                this.showError('Profile not found');
                this.showLoginSection();
            } else {
                this.handleRoutingError(error);
            }
        }
    }

    
    formatDuration(ms) {
        const minutes = Math.floor(ms / 60000);
        const seconds = Math.floor((ms % 60000) / 1000);
        return `${minutes}:${seconds.toString().padStart(2, '0')}`;
    }

    getViewingUserId(path) {
        return path === '/' 
            ? localStorage.getItem('spotify_user_id')
            : path.split('/').filter(Boolean)[0];
    }


    
    handleRoutingError(error) {
        console.error('Failed to load profile:', error);
        this.showError('Failed to load profile. Please try again later.');
        if (!localStorage.getItem('spotify_user_id')) {
            window.location.href = '/';
        }
    }

    showLoginSection() {
        this.toggleSections('login-section', true);
        this.toggleSections('profile-section', false);
        this.toggleSections('user-info', false);
        this.clearIntervals();
        
        // Clear URL if we're showing login
        if (window.location.pathname !== '/') {
            history.pushState({}, '', '/');
        }
    }

    toggleSections(sectionId, show) {
        const section = document.getElementById(sectionId);
        if (section) {
            section.classList.toggle('hidden', !show);
        }
    }

    clearMainContent() {
        // Hide all main sections
        this.toggleSections('login-section', false);
        this.toggleSections('profile-section', false);
        
        // Clear any existing content
        const main = document.querySelector('main');
        const existingContent = main.querySelector('.playlist-view');
        if (existingContent) {
            existingContent.remove();
        }
    }

    async loadPlaylistView(userId, playlistUrl) {
        this.clearMainContent();
        
        const response = await fetch(`${config.backendUrl}/playlists/${playlistUrl}`);
        if (!response.ok) throw new Error('Playlist not found');
        
        const playlist = await response.json();
        document.title = `${playlist.playlist_name} - versz`;
        
        const main = document.querySelector('main');
        const playlistView = document.createElement('div');
        playlistView.className = 'playlist-view animate__animated animate__fadeIn';
        playlistView.innerHTML = `
            <div class="top-bar">
                <a href="/${playlist.owner.profile_url}" class="back-button">←</a>
            </div>

            <div class="container">
                <div class="header">
                    <div class="playlist-cover">
                        <img src="${playlist.cover_image || '/api/placeholder/300/300'}" alt="Playlist cover">
                    </div>
                    <div class="playlist-info">
                        <div class="playlist-type">Playlist</div>
                        <h1 class="playlist-title">${this.escapeHtml(playlist.playlist_name)}</h1>
                        <div class="playlist-meta">
                            Created by ${this.escapeHtml(playlist.owner.display_name)} • 
                            ${playlist.total_tracks} tracks
                        </div>
                    </div>
                </div>

                <ul class="track-list">
                    ${playlist.tracks.map(track => `
                        <li class="track-item">
                            <div class="track-art-container">
                                <img src="${track.album_art || '/api/placeholder/48/48'}" 
                                     alt="Track art" 
                                     class="track-art">
                            </div>
                            <div class="track-info">
                                <div class="track-title">${this.escapeHtml(track.track_name)}</div>
                                <div class="track-artist">${this.escapeHtml(track.artist_name)}</div>
                            </div>
                            <span class="track-duration">${this.formatDuration(track.duration)}</span>
                        </li>
                    `).join('')}
                </ul>
            </div>
        `;
        
        main.appendChild(playlistView);
    }

   async showProfileSection(userData, isOwnProfile) {
        this.toggleSections('login-section', false);
        this.toggleSections('profile-section', true);
    
        // Update profile header with the new optimized design
        const profileHeader = document.querySelector('.profile-header');
        if (profileHeader) {
            profileHeader.innerHTML = `
                <div class="profile-info-container">
                    <img src="${userData.avatar_url || '/api/placeholder/96/96'}" 
                         alt="Profile" 
                         class="profile-avatar"
                         onerror="this.src='/api/placeholder/96/96'">
                    <div class="profile-text">
                        <h1 class="profile-name">${this.escapeHtml(userData.display_name || userData.id)}</h1>
                        <a href="${userData.spotify_url}" 
                           target="_blank" 
                           class="spotify-profile-link">
                            <i class="fab fa-spotify"></i> Spotify Profile
                        </a>
                    </div>
                </div>
            `;
        }
    
        const loggedInUserId = localStorage.getItem('spotify_user_id');
        if (loggedInUserId) {
            await this.updateUserDisplay(loggedInUserId, userData, isOwnProfile);
        }
    
        await this.startTracking(userData.id);
        this.switchTab('recent-tracks');
    
        // Add playlists section
        const contentGrid = document.querySelector('.content-grid');
        if (contentGrid) {
            contentGrid.insertAdjacentHTML('beforeend', `
                <div id="playlists" class="track-card animate__animated animate__fadeIn">
                    <div class="card-header">
                        <h2><i class="fas fa-list"></i> Playlists</h2>
                    </div>
                    <div id="playlists-grid" class="playlists-grid"></div>
                </div>
            `);
    
            await this.updatePlaylists(userData.id);
            this.intervals.playlists = setInterval(() => this.updatePlaylists(userData.id), 300000);
        }
    }

    async updatePlaylists(userId) {
        const playlistsGrid = document.getElementById('playlists-grid');
        if (!playlistsGrid) return;

        try {
            const response = await fetch(`${config.backendUrl}/users/${userId}/playlists`);
            if (!response.ok) throw new Error('Failed to fetch playlists');
            
            const playlists = await response.json();
            playlistsGrid.innerHTML = playlists.map(playlist => `
                <a href="/${userId}/${playlist.url}" class="playlist-item">
                    <div class="playlist-cover">
                        <img src="${playlist.cover_image || '/api/placeholder/150/150'}" 
                             alt="${this.escapeHtml(playlist.name)}"
                             onerror="this.src='/api/placeholder/150/150'">
                    </div>
                    <div class="playlist-name">${this.escapeHtml(playlist.name)}</div>
                    <div class="playlist-tracks">${playlist.total_tracks} tracks</div>
                </a>
            `).join('');
        } catch (error) {
            console.error('Failed to update playlists:', error);
            playlistsGrid.innerHTML = this.createPlaceholder('exclamation-circle', 'Unable to fetch playlists');
        }
    }


    async updateUserDisplay(loggedInUserId, userData, isOwnProfile) {
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

    updateUserInfo(userData) {
        const elements = {
            username: document.getElementById('username'),
            userAvatar: document.getElementById('user-avatar'),
            profileLink: document.getElementById('profile-link')
        };
        
        if (elements.username) elements.username.textContent = userData.display_name || userData.id;
        if (elements.userAvatar) elements.userAvatar.src = userData.avatar_url || 'https://placehold.co/32';
        if (elements.profileLink) elements.profileLink.href = `/${userData.id}`;
    }

    updateProfileInfo(userData) {
        const elements = {
            username: document.getElementById('profile-username'),
            avatar: document.getElementById('profile-avatar')
        };
        
        if (elements.username) elements.username.textContent = userData.display_name || userData.id;
        if (elements.avatar) elements.avatar.src = userData.avatar_url || 'https://placehold.co/96';
        document.title = `${userData.display_name || userData.id} - versz`;
    }

    async startTracking(userId) {
        this.clearIntervals();
    
        try {
            const updates = await Promise.all([
                this.updateCurrentTrack(userId),
                this.updateRecentTracks(userId),
                this.updateTopTracks(userId),    
                this.updateTopArtists(userId)
            ]);

            this.setupTrackingIntervals(userId);
            
            console.log('Tracking started successfully for user:', userId);
        } catch (error) {
            console.error('Error in startTracking:', error);
            this.showError('Failed to start tracking. Please try refreshing the page.');
        }
    }

    setupTrackingIntervals(userId) {
        this.intervals.currentTrack = setInterval(() => this.updateCurrentTrack(userId), 30000);
        this.intervals.recentTracks = setInterval(() => this.updateRecentTracks(userId), 60000);
        this.intervals.topTracks = setInterval(() => this.updateTopTracks(userId), 60000);
        this.intervals.topArtists = setInterval(() => this.updateTopArtists(userId), 60000);
    }

    async updateCurrentTrack(userId) {
        const currentTrackInfo = document.getElementById('current-track-info');
        if (!currentTrackInfo) return;
        
        try {
            const response = await fetch(`${config.backendUrl}/users/${userId}/currently-playing`);
            if (!response.ok) throw new Error('Failed to fetch current track');
            
            const data = await response.json();
            this.renderCurrentTrack(currentTrackInfo, data);
        } catch (error) {
            console.error('Failed to update current track:', error);
            this.renderCurrentTrackError(currentTrackInfo);
        }
    }

    renderCurrentTrack(container, data) {
        if (data.is_playing) {
            container.innerHTML = `
                <div class="track-info">
                    <img src="${data.album_art || 'https://placehold.co/64'}" 
                         alt="Album Art" 
                         class="track-artwork"
                         onerror="this.src='https://placehold.co/64'">
                    <div class="track-details">
                        <div class="track-name">${this.escapeHtml(data.track_name)}</div>
                        <div class="track-artist">${this.escapeHtml(data.artist_name)}</div>
                    </div>
                </div>
            `;
            container.classList.add('playing');
        } else {
            container.innerHTML = this.createPlaceholder('music', 'Not playing anything right now');
            container.classList.remove('playing');
        }
    }

    renderCurrentTrackError(container) {
        container.innerHTML = this.createPlaceholder('exclamation-circle', 'Unable to fetch current track');
    }

    async updateRecentTracks(userId) {
        const elements = {
            list: document.getElementById('tracks-list'),
            count: document.getElementById('tracks-count')
        };
        
        if (!elements.list || !elements.count) return;
        
        try {
            const response = await fetch(`${config.backendUrl}/users/${userId}/recent-tracks`);
            if (!response.ok) throw new Error('Failed to fetch recent tracks');
            
            const tracks = await response.json();
            this.dataCache.recentTracks = tracks;
            
            this.renderRecentTracks(elements, tracks);
        } catch (error) {
            console.error('Failed to update recent tracks:', error);
            this.renderTrackError(elements);
        }
    }

    renderRecentTracks(elements, tracks) {
        elements.count.textContent = tracks.length;
        elements.list.innerHTML = tracks.map(track => this.createTrackItem(track)).join('');
    }

    createTrackItem(track) {
        return `
            <div class="track-item">
                <img src="${track.album_art || 'https://placehold.co/48'}" 
                     alt="Album Art" 
                     class="track-artwork"
                     onerror="this.src='https://placehold.co/48'">
                <div class="track-details">
                    <div class="track-name">${this.escapeHtml(track.track_name)}</div>
                    <div class="track-artist">${this.escapeHtml(track.artist_name)}</div>
                    <div class="track-time">${this.formatDate(track.played_at)}</div>
                </div>
            </div>
        `;
    }

    async updateTopTracks(userId) {
        const elements = {
            list: document.getElementById('top-tracks-list'),
            count: document.getElementById('tracks-count')
        };
        
        if (!elements.list || !elements.count) return;
        
        try {
            const tracks = await this.fetchTopTracks(userId);
            this.dataCache.topTracks = tracks;
            
            if (tracks.length === 0) {
                this.renderEmptyTopTracks(elements);
                return;
            }
            
            this.renderTopTracks(elements, tracks);
        } catch (error) {
            console.error('Failed to update top tracks:', error);
            this.renderTrackError(elements);
        }
    }

    async fetchTopTracks(userId) {
        const response = await fetch(`${config.backendUrl}/users/${userId}/top-tracks`);
        if (!response.ok) {
            throw new Error(`Failed to fetch top tracks: ${response.status}`);
        }
        
        const tracks = await response.json();
        if (!Array.isArray(tracks)) {
            throw new Error('Invalid response format for top tracks');
        }
        
        return tracks;
    }

    renderTopTracks(elements, tracks) {
        elements.count.textContent = tracks.length;
        elements.list.innerHTML = tracks.map((track, index) => this.createTopTrackItem(track, index)).join('');
    }

    createTopTrackItem(track, index) {
        return `
            <div class="track-item">
                <div class="track-rank">${index + 1}</div>
                <img src="${track.album_art || 'https://placehold.co/48'}" 
                     alt="Album Art" 
                     class="track-artwork"
                     onerror="this.src='https://placehold.co/48'">
                <div class="track-details">
                    <div class="track-name">${this.escapeHtml(track.track_name || 'Unknown Track')}</div>
                    <div class="track-artist">${this.escapeHtml(track.artist_name || 'Unknown Artist')}</div>
                    ${track.album_name ? `<div class="track-album">${this.escapeHtml(track.album_name)}</div>` : ''}
                    ${track.popularity ? `<div class="track-popularity">Popularity: ${track.popularity}%</div>` : ''}
                </div>
            </div>
        `;
    }

    async updateTopArtists(userId) {
        const elements = {
            list: document.getElementById('top-artists-list'),
            count: document.getElementById('artists-count')
        };
        
        if (!elements.list || !elements.count) return;
        
        try {
            const artists = await this.fetchTopArtists(userId);
            this.dataCache.topArtists = artists;
            
            if (artists.length === 0) {
                this.renderEmptyTopArtists(elements);
                return;
            }
            
            this.renderTopArtists(elements, artists);
        } catch (error) {
            console.error('Failed to update top artists:', error);
            this.renderArtistError(elements);
        }
    }

    async fetchTopArtists(userId) {
        const response = await fetch(`${config.backendUrl}/users/${userId}/top-artists`);
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.detail || `Failed to fetch top artists: ${response.status}`);
        }
        
        const artists = await response.json();
        if (!Array.isArray(artists)) {
            throw new Error('Invalid response format for top artists');
        }
        
        return artists;
    }

    renderTopArtists(elements, artists) {
        elements.count.textContent = artists.length;
        elements.list.innerHTML = artists.map((artist, index) => this.createArtistItem(artist, index)).join('');
    }

    createArtistItem(artist, index) {
        return `
            <div class="artist-item">
                <div class="artist-rank">${index + 1}</div>
                <img src="${artist.artist_image || 'https://placehold.co/64'}" 
                     alt="Artist" 
                     class="artist-artwork"
                     onerror="this.src='https://placehold.co/64'">
                <div class="artist-details">
                    <div class="artist-name">${this.escapeHtml(artist.artist_name)}</div>
                    <div class="artist-popularity">Popularity: ${artist.popularity}%</div>
                </div>
            </div>
        `;
    }

    renderEmptyTopTracks(elements) {
        elements.list.innerHTML = this.createPlaceholder('music', 'No top tracks available yet');
        elements.count.textContent = '0';
    }

    renderEmptyTopArtists(elements) {
        elements.list.innerHTML = this.createPlaceholder('music', 'No top artists available yet');
        elements.count.textContent = '0';
    }

    renderTrackError(elements) {
        elements.list.innerHTML = this.createPlaceholder('exclamation-circle', 'Unable to fetch tracks');
        elements.count.textContent = '0';
    }

    renderArtistError(elements) {
        elements.list.innerHTML = this.createPlaceholder('exclamation-circle', 'Unable to fetch artists');
        elements.count.textContent = '0';
    }

    createPlaceholder(icon, text) {
        return `
            <div class="placeholder-text">
                <i class="fas fa-${icon}"></i>
                ${text}
            </div>
        `;
    }

    switchTab(targetId) {
        document.querySelectorAll('.tab-content').forEach(content => {
            content.classList.add('hidden');
        });
        
        document.querySelectorAll('.tab-button').forEach(button => {
            button.classList.remove('active');
        });
        
        document.getElementById(targetId)?.classList.remove('hidden');
        document.querySelector(`[data-target="${targetId}"]`)?.classList.add('active');
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

    escapeHtml(unsafe) {
        return unsafe
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
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
