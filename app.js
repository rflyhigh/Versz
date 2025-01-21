class App {
    constructor() {
        this.initializeRouter();
        this.setupEventListeners();
    }

    initializeRouter() {
        router.addRoute('/', HomeView);
        router.addRoute('/dashboard', DashboardView);
        router.addRoute('/editor', EditorView);
        router.addRoute('/editor/:id', EditorView);
        router.addRoute('/:extension', SharedView);
        
        if (Auth.isAuthenticated()) {
            router.navigate('/dashboard');
        } else {
            router.navigate('/');
        }
    }

    setupEventListeners() {
        document.addEventListener('submit', async (e) => {
            if (e.target.id === 'loginForm') {
                e.preventDefault();
                const [username, password] = e.target.querySelectorAll('input');
                if (await Auth.login(username.value, password.value)) {
                    router.navigate('/dashboard');
                }
            }
            
            if (e.target.id === 'registerForm') {
                e.preventDefault();
                const [username, name, password] = e.target.querySelectorAll('input');
                if (await Auth.register(username.value, name.value, password.value)) {
                    router.navigate('/dashboard');
                }
            }
        });
    }
}

// Initialize the application
window.addEventListener('DOMContentLoaded', () => {
    window.app = new App();
});
