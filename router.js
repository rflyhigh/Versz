class Router {
    constructor() {
        this.routes = {};
        window.addEventListener('popstate', () => this.handleRoute());
        document.addEventListener('click', e => {
            if (e.target.matches('[data-link]')) {
                e.preventDefault();
                this.navigate(e.target.href);
            }
        });
    }

    addRoute(path, component) {
        this.routes[path] = component;
    }

    navigate(url) {
        history.pushState(null, null, url);
        this.handleRoute();
    }

    async handleRoute() {
        const path = window.location.pathname;
        const Component = this.routes[path] || this.routes['/404'];
        document.getElementById('app').innerHTML = await Component();
    }
}

const router = new Router();
