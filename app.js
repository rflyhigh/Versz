class VerszApp {
    constructor() {
        try {
            // Basic state initialization
            this.state = {
                isLoading: false,
                currentUser: null,
                viewingUser: null,
                currentTrack: null,
                error: null,
                initialized: false
            };

            // Initialize caches
            this.cache = {
                recentTracks: new Map(),
                topTracks: new Map(),
                topArtists: new Map(),
                playlists: new Map(),
                users: new Map()
            };

            // Initialize intervals container
            this.intervals = {
                currentTrack: null,
                recentTracks: null,
                topTracks: null,
                topArtists: null,
                playlists: null
            };

            // Initialize timeouts
            this.timeouts = {
                search: null,
                urlCheck: null,
                error: null
            };

            // Safe initialization
            this.safeInitialize();

        } catch (error) {
            console.error('Constructor error:', error);
            this.handleFatalError('Failed to initialize application');
        }
    }
    async safeInitialize() {
        try {
            // Show loading state
            this.showLoadingOverlay();

            // Initialize components one by one
            await this.initializeComponents();

            // Hide loading when done
            this.hideLoadingOverlay();

            // Mark as initialized
            this.state.initialized = true;

        } catch (error) {
            console.error('Initialization error:', error);
            this.handleFatalError('Failed to initialize application');
        }
    }
    async initializeComponents() {
        try {
            // Setup error handlers first
            this.setupErrorHandling();

            // Check for existing session
            await this.checkExistingSession();

            // Setup event listeners
            this.setupEventListeners();

            // Setup search functionality
            this.setupSearch();

            // Handle any redirect path
            this.handleRedirectPath();

            // Check for auth callback
            await this.checkAuthCallback();

            // Handle initial routing
            await this.handleRouting();

        } catch (error) {
            throw new Error(`Component initialization failed: ${error.message}`);
        }
    }
    setupErrorHandling() {
        window.addEventListener('error', (event) => {
            console.error('Global error:', event.error);
            this.handleError('An unexpected error occurred');
        });

        window.addEventListener('unhandledrejection', (event) => {
            console.error('Unhandled promise rejection:', event.reason);
            this.handleError('An unexpected error occurred');
        });
    }

    setupEventListeners() {
        try {
            // Global click handler
            document.addEventListener('click', (e) => this.handleGlobalClick(e));

            // Navigation handling
            window.addEventListener('popstate', () => this.handleRouting());

            // Window focus handling
            window.addEventListener('focus', () => this.handleWindowFocus());

            // Form submission prevention
            document.querySelectorAll('form').forEach(form => {
                form.addEventListener('submit', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                });
            });

        } catch (error) {
            throw new Error(`Event listener setup failed: ${error.message}`);
        }
    }

    async initializeApp() {
        try {
            await Promise.all([
                this.handleRedirectPath(),
                this.setupEventListeners(),
                this.checkAuthCallback(),
                this.checkExistingSession()
            ]);

            this.setupSearch();
            await this.handleRouting();

            // Add window focus handling for better real-time updates
            window.addEventListener('focus', () => this.handleWindowFocus());
        } catch (error) {
            throw new Error(`Initialization failed: ${error.message}`);
        }
    }

    handleWindowFocus() {
        const userId = this.state.viewingUser?.id;
        if (userId) {
            this.refreshUserData(userId).catch(error => {
                console.error('Failed to refresh user data:', error);
            });
        }
    }

    async refreshUserData(userId) {
        try {
            await Promise.all([
                this.updateCurrentTrack(userId),
                this.updateRecentTracks(userId),
                this.updateTopTracks(userId),
                this.updateTopArtists(userId),
                this.updatePlaylists(userId)
            ]);
        } catch (error) {
            throw new Error(`Data refresh failed: ${error.message}`);
        }
    }

    handleRedirectPath() {
        const redirectPath = sessionStorage.getItem('redirect_path');
        if (redirectPath) {
            sessionStorage.removeItem('redirect_path');
            history.replaceState(null, '', redirectPath);
        }
    }

    handleError(message) {
        console.error('Application error:', message);
        this.showError(message);
        this.recoverFromError().catch(console.error);
    }

    handleFatalError(message) {
        console.error('Fatal error:', message);
        this.hideLoadingOverlay();
        this.showError(message || 'A fatal error occurred. Please refresh the page.');
        this.state.error = true;
    }

    showLoadingOverlay() {
        const overlay = document.getElementById('loading-overlay');
        if (overlay) {
            overlay.classList.remove('hidden');
        }
    }

    hideLoadingOverlay() {
        const overlay = document.getElementById('loading-overlay');
        if (overlay) {
            overlay.classList.add('hidden');
        }
    }

    showError(message) {
        const errorContainer = document.getElementById('error-container');
        if (!errorContainer) return;

        const errorElement = document.createElement('div');
        errorElement.className = 'error-message animate__animated animate__fadeIn';
        errorElement.textContent = message;

        // Add close button
        const closeButton = document.createElement('button');
        closeButton.className = 'error-close';
        closeButton.innerHTML = '&times;';
        closeButton.onclick = () => errorElement.remove();
        errorElement.appendChild(closeButton);

        errorContainer.appendChild(errorElement);

        // Auto-remove after 5 seconds
        setTimeout(() => {
            errorElement.remove();
        }, 5000);
    }

    async recoverFromError() {
        try {
            this.clearIntervals();
            this.clearTimeouts();
            
            if (this.state.error) {
                this.showLoginSection();
                return;
            }

            const userId = localStorage.getItem('spotify_user_id');
            if (userId) {
                await this.validateUserSession(userId);
            } else {
                this.showLoginSection();
            }
        } catch (error) {
            console.error('Recovery failed:', error);
            this.logout();
        }
    }

    clearIntervals() {
        Object.values(this.intervals).forEach(interval => {
            if (interval) clearInterval(interval);
        });
    }

    clearTimeouts() {
        Object.values(this.timeouts).forEach(timeout => {
            if (timeout) clearTimeout(timeout);
        });
    }

    destroy() {
        try {
            this.clearIntervals();
            this.clearTimeouts();
            window.removeEventListener('popstate', () => this.handleRouting());
            window.removeEventListener('focus', () => this.handleWindowFocus());
            document.removeEventListener('click', (e) => this.handleGlobalClick(e));
            this.state = null;
            this.cache = null;
        } catch (error) {
            console.error('Cleanup error:', error);
        }
    }



    handleGlobalClick(e) {
        // Delegate click handling for better performance
        const target = e.target;

        // Handle search container clicks
        if (!target.closest('#search-container')) {
            this.hideSearchResults();
        }

        // Handle login buttons
        if (target.matches('#quick-login-btn')) {
            this.login();
        } else if (target.matches('#custom-login-btn')) {
            this.login(true);
        } else if (target.matches('#custom-url-toggle')) {
            this.toggleCustomUrlSection();
        } else if (target.matches('#logout-btn')) {
            this.logout();
        }

        // Handle tab switching
        const tabButton = target.closest('.tab-button');
        if (tabButton) {
            this.switchTab(tabButton.dataset.target);
        }

        // Handle error message dismissal
        if (target.closest('.error-message')) {
            target.closest('.error-message').remove();
        }
    }
    async login(useCustomUrl = false) {
        try {
            if (this.state.isLoading) return;
            this.state.isLoading = true;

            let customUrl = '';
            if (useCustomUrl) {
                const urlInput = document.getElementById('custom-url-input');
                if (!urlInput || urlInput.dataset.valid !== 'true') {
                    throw new Error('Please choose a valid URL');
                }
                customUrl = urlInput.value.trim();
            }

            const state = this.generateSecureState();
            await this.setLoginStorage(state, customUrl);
            
            const redirectUri = `${window.location.origin}/callback.html`;
            const authUrl = this.buildSpotifyAuthUrl(state, redirectUri);
            
            window.location.href = authUrl;
        } catch (error) {
            this.showError(error.message);
        } finally {
            this.state.isLoading = false;
        }
    }

    generateSecureState() {
        const array = new Uint32Array(2);
        crypto.getRandomValues(array);
        return Array.from(array, dec => dec.toString(36)).join('');
    }

    buildSpotifyAuthUrl(state, redirectUri) {
        const params = new URLSearchParams({
            client_id: config.clientId,
            response_type: 'code',
            redirect_uri: redirectUri,
            scope: config.scopes,
            state: state
        });
        return `https://accounts.spotify.com/authorize?${params.toString()}`;
    }

    async setLoginStorage(state, customUrl) {
        try {
            localStorage.setItem('spotify_auth_state', state);
            localStorage.setItem('login_pending', 'true');
            if (customUrl) {
                localStorage.setItem('pending_custom_url', customUrl);
            }
        } catch (error) {
            throw new Error('Failed to set login storage. Please enable cookies and try again.');
        }
    }

    async checkAuthCallback() {
        try {
            const urlParams = new URLSearchParams(window.location.search);
            const code = urlParams.get('code');
            const state = urlParams.get('state');

            if (!code) return;

            if (!this.validateAuthState(state)) {
                throw new Error('Authentication failed: Invalid state parameter');
            }

            await this.processAuthCallback(code);
        } catch (error) {
            this.handleAuthError(error);
        }
    }

    validateAuthState(state) {
        const storedState = localStorage.getItem('spotify_auth_state');
        return state === storedState && state !== null;
    }

    async processAuthCallback(code) {
        try {
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
                await this.handleSuccessfulAuth(data.user_id);
            } else {
                throw new Error('Authentication response was not successful');
            }
        } catch (error) {
            throw new Error(`Auth callback processing failed: ${error.message}`);
        }
    }

    async handleSuccessfulAuth(userId) {
        try {
            localStorage.setItem('spotify_user_id', userId);
            this.clearAuthStorage();
            this.state.currentUser = userId;
            window.location.href = '/';
        } catch (error) {
            throw new Error('Failed to complete authentication process');
        }
    }

    handleAuthError(error) {
        console.error('Authentication error:', error);
        this.showError(error.message || 'Authentication failed. Please try again.');
        this.clearAuthStorage();
    }

    clearAuthStorage() {
        const authKeys = ['login_pending', 'spotify_auth_state', 'pending_custom_url'];
        authKeys.forEach(key => {
            try {
                localStorage.removeItem(key);
            } catch (error) {
                console.error(`Failed to remove ${key} from storage:`, error);
            }
        });
    }

    logout() {
        try {
            this.clearStorage();
            this.clearIntervals();
            this.resetState();
            window.location.href = '/';
        } catch (error) {
            console.error('Logout failed:', error);
            window.location.reload(); // Fallback: force page reload
        }
    }

    resetState() {
        this.state = {
            isLoading: false,
            currentUser: null,
            viewingUser: null,
            currentTrack: null
        };
    }

    clearStorage() {
        const keys = ['spotify_user_id', 'login_pending', 'spotify_auth_state', 'pending_custom_url'];
        keys.forEach(key => {
            try {
                localStorage.removeItem(key);
            } catch (error) {
                console.error(`Failed to remove ${key} from storage:`, error);
            }
        });
    }

    async checkExistingSession() {
        try {
            const userId = localStorage.getItem('spotify_user_id');
            const loginPending = localStorage.getItem('login_pending');

            if (!userId || loginPending) {
                const path = window.location.pathname;
                if (path !== '/') {
                    const viewingUserId = this.getViewingUserId(path);
                    try {
                        await this.loadProfile(viewingUserId);
                    } catch (error) {
                        this.showLoginSection();
                        this.showError('User not found');
                    }
                    return;
                }
                this.showLoginSection();
                return;
            }

            await this.validateUserSession(userId);
        } catch (error) {
            console.error('Session check failed:', error);
            this.handleSessionError();
        }
    }

    async validateUserSession(userId) {
        try {
            const response = await fetch(`${config.backendUrl}/users/${userId}`);
            if (!response.ok) throw new Error('Invalid session');
            
            const userData = await response.json();
            this.state.currentUser = userData;
            await this.handleRouting();
        } catch (error) {
            throw new Error(`Session validation failed: ${error.message}`);
        }
    }

    handleSessionError() {
        this.logout();
        this.showError('Your session has expired. Please log in again.');
    }

    async loadProfile(userId) {
        try {
            if (this.cache.users.has(userId)) {
                const cachedData = this.cache.users.get(userId);
                if (Date.now() - cachedData.timestamp < 300000) { // 5 minutes cache
                    await this.showProfileSection(cachedData.data, userId === this.state.currentUser?.id);
                    return;
                }
            }

            const response = await fetch(`${config.backendUrl}/users/${userId}`);
            if (!response.ok) {
                throw new Error(response.status === 404 ? 'User not found' : 'Failed to load profile');
            }
            
            const userData = await response.json();
            this.cache.users.set(userId, {
                data: userData,
                timestamp: Date.now()
            });

            const isOwnProfile = userId === this.state.currentUser?.id;
            await this.showProfileSection(userData, isOwnProfile);
        } catch (error) {
            throw new Error(`Profile loading failed: ${error.message}`);
        }
    }

    async showProfileSection(userData, isOwnProfile) {
        try {
            this.toggleSections('login-section', false);
            this.toggleSections('profile-section', true);
            
            const loggedInUserId = localStorage.getItem('spotify_user_id');
            if (loggedInUserId) {
                await this.updateUserDisplay(loggedInUserId, userData, isOwnProfile);
            }

            this.updateProfileInfo(userData);
            this.state.viewingUser = userData;
            await this.startTracking(userData.id);
            this.switchTab('recent-tracks');
        } catch (error) {
            console.error('Failed to show profile section:', error);
            this.showError('Failed to load profile content');
        }
    }

    async updateUserDisplay(loggedInUserId, userData, isOwnProfile) {
        try {
            const userInfo = document.getElementById('user-info');
            if (!userInfo) return;

            userInfo.classList.remove('hidden');
            
            if (!isOwnProfile) {
                const response = await fetch(`${config.backendUrl}/users/${loggedInUserId}`);
                if (response.ok) {
                    const loggedInUserData = await response.json();
                    this.updateUserInfo(loggedInUserData);
                }
            } else {
                this.updateUserInfo(userData);
            }
        } catch (error) {
            console.error('Failed to update user display:', error);
        }
    }

    updateUserInfo(userData) {
        const elements = {
            username: document.getElementById('username'),
            userAvatar: document.getElementById('user-avatar'),
            profileLink: document.getElementById('profile-link')
        };
        
        if (elements.username) {
            elements.username.textContent = userData.display_name || userData.id;
        }
        if (elements.userAvatar) {
            elements.userAvatar.src = userData.avatar_url || 'https://placehold.co/32';
            elements.userAvatar.onerror = () => {
                elements.userAvatar.src = 'https://placehold.co/32';
            };
        }
        if (elements.profileLink) {
            elements.profileLink.href = `/${userData.id}`;
        }
    }

    updateProfileInfo(userData) {
        const elements = {
            username: document.getElementById('profile-username'),
            avatar: document.getElementById('profile-avatar')
        };
        
        if (elements.username) {
            elements.username.textContent = userData.display_name || userData.id;
        }
        if (elements.avatar) {
            elements.avatar.src = userData.avatar_url || 'https://placehold.co/96';
            elements.avatar.onerror = () => {
                elements.avatar.src = 'https://placehold.co/96';
            };
        }
        
        document.title = `${userData.display_name || userData.id} - versz`;
    }

    async startTracking(userId) {
        try {
            this.clearIntervals();
            
            await Promise.all([
                this.updateCurrentTrack(userId),
                this.updateRecentTracks(userId),
                this.updateTopTracks(userId),
                this.updateTopArtists(userId),
                this.updatePlaylists(userId)
            ]);

            this.setupTrackingIntervals(userId);
        } catch (error) {
            console.error('Failed to start tracking:', error);
            this.showError('Failed to load user data. Please refresh the page.');
        }
    }

    setupTrackingIntervals(userId) {
        const intervals = {
            currentTrack: 30000,
            recentTracks: 60000,
            topTracks: 60000,
            topArtists: 60000,
            playlists: 300000
        };

        Object.entries(intervals).forEach(([key, interval]) => {
            this.intervals[key] = setInterval(() => {
                this[`update${key.charAt(0).toUpperCase() + key.slice(1)}`](userId)
                    .catch(error => console.error(`Failed to update ${key}:`, error));
            }, interval);
        });
    }
    async updateCurrentTrack(userId) {
        const currentTrackInfo = document.getElementById('current-track-info');
        if (!currentTrackInfo) return;

        try {
            const response = await this.fetchWithTimeout(
                `${config.backendUrl}/users/${userId}/currently-playing`,
                { timeout: 5000 }
            );
            
            if (!response.ok) throw new Error('Failed to fetch current track');
            
            const data = await response.json();
            this.state.currentTrack = data;
            this.renderCurrentTrack(currentTrackInfo, data);
        } catch (error) {
            console.error('Current track update failed:', error);
            this.renderCurrentTrackError(currentTrackInfo);
        }
    }

    async fetchWithTimeout(url, options = {}) {
        const { timeout = 5000 } = options;
        const controller = new AbortController();
        const id = setTimeout(() => controller.abort(), timeout);

        try {
            const response = await fetch(url, {
                ...options,
                signal: controller.signal
            });
            clearTimeout(id);
            return response;
        } catch (error) {
            clearTimeout(id);
            throw error;
        }
    }

    renderCurrentTrack(container, data) {
        if (!data || !container) return;

        if (data.is_playing) {
            const trackHtml = this.createTrackPlayingHtml(data);
            container.innerHTML = trackHtml;
            container.classList.add('playing');
            this.initializeTrackAnimations(container);
        } else {
            container.innerHTML = this.createPlaceholder('music', 'Not playing anything right now');
            container.classList.remove('playing');
        }
    }

    createTrackPlayingHtml(data) {
        return `
            <div class="track-info">
                <img src="${this.getSecureImageUrl(data.album_art)}" 
                     alt="Album Art" 
                     class="track-artwork"
                     onerror="this.src='https://placehold.co/64'">
                <div class="track-details">
                    <div class="track-name">${this.escapeHtml(data.track_name)}</div>
                    <div class="track-artist">${this.escapeHtml(data.artist_name)}</div>
                    ${data.spotify_url ? `
                        <a href="${data.spotify_url}" 
                           target="_blank" 
                           class="spotify-link"
                           rel="noopener noreferrer">
                            <i class="fab fa-spotify"></i> Open in Spotify
                        </a>
                    ` : ''}
                </div>
            </div>
        `;
    }

    initializeTrackAnimations(container) {
        const artwork = container.querySelector('.track-artwork');
        if (artwork) {
            artwork.addEventListener('load', () => {
                artwork.classList.add('loaded');
            });
        }
    }

    async updateRecentTracks(userId) {
        const elements = {
            list: document.getElementById('tracks-list'),
            count: document.getElementById('tracks-count')
        };
        
        if (!elements.list || !elements.count) return;

        try {
            const response = await this.fetchWithTimeout(
                `${config.backendUrl}/users/${userId}/recent-tracks`
            );
            
            if (!response.ok) throw new Error('Failed to fetch recent tracks');
            
            const tracks = await response.json();
            this.cache.recentTracks.set(userId, {
                data: tracks,
                timestamp: Date.now()
            });
            
            this.renderRecentTracks(elements, tracks);
        } catch (error) {
            console.error('Recent tracks update failed:', error);
            this.renderTrackError(elements);
        }
    }

    renderRecentTracks(elements, tracks) {
        if (!tracks || !Array.isArray(tracks)) {
            this.renderTrackError(elements);
            return;
        }

        elements.count.textContent = tracks.length;
        elements.list.innerHTML = tracks.map(track => 
            this.createTrackItem(track)
        ).join('');

        this.initializeTrackInteractions(elements.list);
    }

    initializeTrackInteractions(container) {
        container.querySelectorAll('.track-item').forEach(item => {
            item.addEventListener('click', () => {
                const spotifyUrl = item.dataset.spotifyUrl;
                if (spotifyUrl) {
                    window.open(spotifyUrl, '_blank', 'noopener,noreferrer');
                }
            });
        });
    }

    createTrackItem(track) {
        if (!track) return '';

        return `
            <div class="track-item" 
                 data-spotify-url="${track.spotify_url || ''}"
                 data-track-id="${track.id || ''}">
                <img src="${this.getSecureImageUrl(track.album_art)}" 
                     alt="Album Art" 
                     class="track-artwork"
                     loading="lazy"
                     onerror="this.src='https://placehold.co/48'">
                <div class="track-details">
                    <div class="track-name">${this.escapeHtml(track.track_name)}</div>
                    <div class="track-artist">${this.escapeHtml(track.artist_name)}</div>
                    <div class="track-meta">
                        <span class="track-time">${this.formatDate(track.played_at)}</span>
                        ${track.popularity ? `
                            <div class="track-popularity">
                                <div class="popularity-bar" 
                                     style="width: ${track.popularity}%"></div>
                            </div>
                        ` : ''}
                    </div>
                </div>
            </div>
        `;
    }

    getSecureImageUrl(url) {
        if (!url) return 'https://placehold.co/48';
        try {
            const secureUrl = new URL(url);
            secureUrl.protocol = 'https:';
            return secureUrl.toString();
        } catch {
            return 'https://placehold.co/48';
        }
    }

    formatDate(dateString) {
        if (!dateString) return '';
        
        try {
            const date = new Date(dateString);
            const now = new Date();
            const diff = Math.floor((now - date) / 1000);
            
            if (diff < 60) return 'Just now';
            if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
            if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
            if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
            
            return date.toLocaleDateString(undefined, {
                year: 'numeric',
                month: 'short',
                day: 'numeric'
            });
        } catch (error) {
            console.error('Date formatting error:', error);
            return 'Unknown date';
        }
    }

    renderTrackError(elements) {
        if (elements.list) {
            elements.list.innerHTML = this.createPlaceholder(
                'exclamation-circle',
                'Unable to load tracks'
            );
        }
        if (elements.count) {
            elements.count.textContent = '0';
        }
    }
    async updateTopTracks(userId) {
        const elements = {
            list: document.getElementById('top-tracks-list'),
            count: document.getElementById('tracks-count')
        };
        
        if (!elements.list || !elements.count) return;

        try {
            const cachedData = this.cache.topTracks.get(userId);
            if (cachedData && (Date.now() - cachedData.timestamp) < 300000) {
                this.renderTopTracks(elements, cachedData.data);
                return;
            }

            const tracks = await this.fetchTopTracks(userId);
            this.cache.topTracks.set(userId, {
                data: tracks,
                timestamp: Date.now()
            });
            
            this.renderTopTracks(elements, tracks);
        } catch (error) {
            console.error('Top tracks update failed:', error);
            this.renderTrackError(elements);
        }
    }

    async fetchTopTracks(userId) {
        const response = await this.fetchWithTimeout(
            `${config.backendUrl}/users/${userId}/top-tracks`,
            { timeout: 8000 }
        );

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
        if (!tracks || !Array.isArray(tracks)) {
            this.renderEmptyTopTracks(elements);
            return;
        }

        elements.count.textContent = tracks.length;
        elements.list.innerHTML = tracks
            .map((track, index) => this.createTopTrackItem(track, index))
            .join('');

        this.initializeTrackInteractions(elements.list);
    }

    createTopTrackItem(track, index) {
        if (!track) return '';

        return `
            <div class="track-item" 
                 data-spotify-url="${track.spotify_url || ''}"
                 data-track-id="${track.id || ''}">
                <div class="track-rank">${index + 1}</div>
                <img src="${this.getSecureImageUrl(track.album_art)}" 
                     alt="Album Art" 
                     class="track-artwork"
                     loading="lazy"
                     onerror="this.src='https://placehold.co/48'">
                <div class="track-details">
                    <div class="track-name">${this.escapeHtml(track.track_name || 'Unknown Track')}</div>
                    <div class="track-artist">${this.escapeHtml(track.artist_name || 'Unknown Artist')}</div>
                    ${track.album_name ? 
                        `<div class="track-album">${this.escapeHtml(track.album_name)}</div>` : ''}
                    ${this.createPopularityBar(track.popularity)}
                </div>
            </div>
        `;
    }

    createPopularityBar(popularity) {
        if (!popularity && popularity !== 0) return '';

        return `
            <div class="popularity-wrapper">
                <div class="popularity-bar-container">
                    <div class="popularity-bar" style="width: ${popularity}%"></div>
                </div>
                <span class="popularity-label">${popularity}%</span>
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
            const cachedData = this.cache.topArtists.get(userId);
            if (cachedData && (Date.now() - cachedData.timestamp) < 300000) {
                this.renderTopArtists(elements, cachedData.data);
                return;
            }

            const artists = await this.fetchTopArtists(userId);
            this.cache.topArtists.set(userId, {
                data: artists,
                timestamp: Date.now()
            });
            
            this.renderTopArtists(elements, artists);
        } catch (error) {
            console.error('Top artists update failed:', error);
            this.renderArtistError(elements);
        }
    }

    async fetchTopArtists(userId) {
        const response = await this.fetchWithTimeout(
            `${config.backendUrl}/users/${userId}/top-artists`,
            { timeout: 8000 }
        );

        if (!response.ok) {
            throw new Error(`Failed to fetch top artists: ${response.status}`);
        }
        
        const artists = await response.json();
        if (!Array.isArray(artists)) {
            throw new Error('Invalid response format for top artists');
        }
        
        return artists;
    }

    renderTopArtists(elements, artists) {
        if (!artists || !Array.isArray(artists)) {
            this.renderEmptyTopArtists(elements);
            return;
        }

        elements.count.textContent = artists.length;
        elements.list.innerHTML = artists
            .map((artist, index) => this.createArtistItem(artist, index))
            .join('');

        this.initializeArtistInteractions(elements.list);
    }

    createArtistItem(artist, index) {
        if (!artist) return '';

        return `
            <div class="artist-item" 
                 data-spotify-url="${artist.spotify_url || ''}"
                 data-artist-id="${artist.id || ''}">
                <div class="artist-rank">${index + 1}</div>
                <img src="${this.getSecureImageUrl(artist.artist_image)}" 
                     alt="Artist" 
                     class="artist-artwork"
                     loading="lazy"
                     onerror="this.src='https://placehold.co/64'">
                <div class="artist-details">
                    <div class="artist-name">${this.escapeHtml(artist.artist_name)}</div>
                    ${this.createPopularityBar(artist.popularity)}
                    ${artist.genres ? 
                        `<div class="artist-genres">${this.renderGenres(artist.genres)}</div>` : ''}
                </div>
            </div>
        `;
    }

    renderGenres(genres) {
        if (!genres || !Array.isArray(genres)) return '';
        
        return genres
            .slice(0, 3)
            .map(genre => `<span class="genre-tag">${this.escapeHtml(genre)}</span>`)
            .join('');
    }

    initializeArtistInteractions(container) {
        if (!container) return;

        container.querySelectorAll('.artist-item').forEach(item => {
            item.addEventListener('click', () => {
                const spotifyUrl = item.dataset.spotifyUrl;
                if (spotifyUrl) {
                    window.open(spotifyUrl, '_blank', 'noopener,noreferrer');
                }
            });
        });
    }

    async updatePlaylists(userId) {
        const playlistsContainer = document.getElementById('playlists-list');
        if (!playlistsContainer) return;

        try {
            const cachedData = this.cache.playlists.get(userId);
            if (cachedData && (Date.now() - cachedData.timestamp) < 300000) {
                this.renderPlaylists(playlistsContainer, cachedData.data);
                return;
            }

            const response = await this.fetchWithTimeout(
                `${config.backendUrl}/users/${userId}/playlists`
            );

            if (!response.ok) throw new Error('Failed to fetch playlists');
            
            const playlists = await response.json();
            this.cache.playlists.set(userId, {
                data: playlists,
                timestamp: Date.now()
            });
            
            this.renderPlaylists(playlistsContainer, playlists);
        } catch (error) {
            console.error('Playlists update failed:', error);
            this.renderPlaylistsError(playlistsContainer);
        }
    }
    renderPlaylists(container, playlists) {
        if (!playlists || !Array.isArray(playlists) || playlists.length === 0) {
            container.innerHTML = this.createPlaceholder('music', 'No playlists available');
            return;
        }

        container.innerHTML = playlists
            .map(playlist => this.createPlaylistItem(playlist))
            .join('');

        this.initializePlaylistInteractions(container);
    }

    createPlaylistItem(playlist) {
        if (!playlist) return '';

        return `
            <a href="/${this.getViewingUserId(window.location.pathname)}/${playlist.url}" 
               class="playlist-item"
               data-playlist-id="${playlist.id || ''}"
               title="${this.escapeHtml(playlist.name)}">
                <div class="playlist-artwork-container">
                    <img src="${this.getSecureImageUrl(playlist.cover_image)}" 
                         alt="Playlist Cover" 
                         class="playlist-artwork"
                         loading="lazy"
                         onerror="this.src='https://placehold.co/96'">
                    <div class="playlist-overlay">
                        <i class="fas fa-play"></i>
                    </div>
                </div>
                <div class="playlist-details">
                    <div class="playlist-name">${this.escapeHtml(playlist.name)}</div>
                    <div class="playlist-meta">
                        <span class="playlist-tracks">${playlist.total_tracks} tracks</span>
                        ${playlist.is_public ? 
                            '<span class="playlist-visibility">Public</span>' : 
                            '<span class="playlist-visibility">Private</span>'}
                    </div>
                </div>
            </a>
        `;
    }

    initializePlaylistInteractions(container) {
        if (!container) return;

        container.querySelectorAll('.playlist-item').forEach(item => {
            item.addEventListener('mouseenter', () => {
                item.querySelector('.playlist-overlay')?.classList.add('visible');
            });

            item.addEventListener('mouseleave', () => {
                item.querySelector('.playlist-overlay')?.classList.remove('visible');
            });
        });
    }

    renderPlaylistsError(container) {
        if (!container) return;
        
        container.innerHTML = this.createPlaceholder(
            'exclamation-circle',
            'Unable to load playlists'
        );
    }

    // Utility Functions
    createPlaceholder(icon, text) {
        return `
            <div class="placeholder-text">
                <i class="fas fa-${icon}"></i>
                <span>${this.escapeHtml(text)}</span>
            </div>
        `;
    }

    escapeHtml(unsafe) {
        if (!unsafe) return '';
        
        return unsafe
            .toString()
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }



    dismissError(errorElement) {
        if (!errorElement) return;

        errorElement.classList.add('animate__fadeOut');
        setTimeout(() => errorElement.remove(), 300);
    }

    // Navigation and Routing
    getViewingUserId(path) {
        if (!path) return '';
        return path === '/' 
            ? localStorage.getItem('spotify_user_id')
            : path.split('/').filter(Boolean)[0];
    }

    async handleRouting() {
        try {
            const path = window.location.pathname;
            const pathParts = path.split('/').filter(Boolean);
            
            // Clear any existing intervals before setting up new ones
            this.clearIntervals();
    
            if (pathParts.length === 0) {
                const userId = localStorage.getItem('spotify_user_id');
                if (!userId) {
                    this.showLoginSection();
                    return;
                }
                await this.navigateToProfile(userId);
                return;
            }
    
            // Handle playlist view
            if (pathParts.length === 2) {
                const [userId, playlistId] = pathParts;
                try {
                    await this.loadPlaylist(userId, playlistId);
                    return;
                } catch (error) {
                    console.error('Failed to load playlist:', error);
                    this.showError('Failed to load playlist');
                    history.pushState(null, '', `/${userId}`);
                    await this.loadProfile(userId);
                    return;
                }
            }
    
            // Handle profile view
            const viewingUserId = pathParts[0];
            try {
                await this.loadProfile(viewingUserId);
            } catch (error) {
                console.error('Failed to load profile:', error);
                this.showError('Profile not found');
                this.showLoginSection();
            }
        } catch (error) {
            console.error('Routing error:', error);
            this.handleRoutingError(error);
        }
    }

    // Section Toggle Utilities
    toggleSections(showSection) {
        const sections = ['login-section', 'profile-section', 'playlist-detail'];
        
        sections.forEach(section => {
            const element = document.getElementById(section);
            if (!element) return;
    
            if (section === showSection) {
                element.classList.remove('hidden');
                element.classList.add('animate__fadeIn');
            } else {
                element.classList.add('hidden');
                element.classList.remove('animate__fadeIn');
            }
        });
    }

    // Performance Optimization
    debounce(func, wait) {
        let timeout;
        return (...args) => {
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(this, args), wait);
        };
    }



    removeEventListeners() {
        window.removeEventListener('popstate', this.handleRouting.bind(this));
        window.removeEventListener('focus', this.handleWindowFocus.bind(this));
    }
}

// Initialize the app when the DOM is fully loaded
document.addEventListener('DOMContentLoaded', () => {
    try {
        window.app = new VerszApp();
    } catch (error) {
        console.error('Failed to create app instance:', error);
        const errorContainer = document.getElementById('error-container');
        if (errorContainer) {
            errorContainer.innerHTML = '<div class="error-message">Failed to initialize application. Please refresh the page.</div>';
        }
    }
});

// Cleanup on page unload
window.addEventListener('unload', () => {
    if (window.app) {
        window.app.destroy();
    }
});
