// Home page setup code.
//
//      Connor Shugg

// Document globals
const content = document.getElementById("content");
const tab_anchors = document.getElementById("tab_anchors");

// Other globals
const hostname = window.location.hostname;

// Services
const services = [
    new Service("pihole", "Pi-hole", new URL(hostname, "2301", "/admin/index.php")),
    new Service("jellyfin", "Jellyfin", new URL(hostname, "2302", "/NEED_TO_ACTUALLY_MAKE_THIS"))
];

// Window-load function
window.onload = function()
{
    // TAB 1: OVERVIEW
    const tab_overview = new Tab("overview", true);
    tab_overview.set_title("Overview");
    content.appendChild(tab_overview.html);
    tab_anchors.appendChild(tab_overview.anchor);

    // TAB 2: HOME LIGHTING
    const tab_lighting = new Tab("lighting", false);
    tab_lighting.set_title("Home Lighting");
    content.appendChild(tab_lighting.html);
    tab_anchors.appendChild(tab_lighting.anchor);

    // initialize all tabs and their inner content
    init_tab_overview(tab_overview);
    init_tab_lighting(tab_lighting);
}

// ============================== Lighting Tab ============================== //
// Initializes the lighting tab and everything within.
function init_tab_lighting(tab_lighting)
{
    // TODO - replace this with actual lighting services
    const card = new Card("card_lighting1");
    card.set_title("Reachable Lights");
    tab_lighting.add_card(card);

    // TODO - replace this with actual lighting services
}

// ============================== Overview Tab ============================== //
// Initializes the given tab as the main "overview" tab.
function init_tab_overview(tab_overview)
{
    // add an initial card to the overview tab
    const card_main = new Card("card_main");
    card_main.set_title("Welcome");
    tab_overview.add_card(card_main);
    init_card_main(card_main);
}

// Initializes the main card on the home page.
function init_card_main(card_main)
{
    // add introductory text to the card
    const p1 = document.createElement("p");
    p1.innerHTML = "This is my home server. It's under construction.";
    card_main.add_html(p1);

    // add a list of services to the div
    const srvs_header = document.createElement("h3");
    srvs_header.innerHTML = "Services";
    card_main.add_html(srvs_header);

    // create the services list
    const srvs_list = document.createElement("ul");
    for (let i = 0; i < services.length; i++)
    {
        const srv = services[i];
        const li = document.createElement("li");
        li.id = srv.id + "_list_item";

        // append the anchor to the service and some extra text
        li.appendChild(srv.make_anchor(srv.name));
        li.innerHTML += " (running on port " + srv.url.port + ")";
        srvs_list.appendChild(li);
    }
    card_main.add_html(srvs_list);
}

