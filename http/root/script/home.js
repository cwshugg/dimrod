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
    new Service("pihole", "Pi-hole",
                new URL(hostname, "2301", "/admin/index.php"),
                "fa fa-network-wired", "mdl-color-text--red-400"),
    new Service("jellyfin", "Jellyfin",
                new URL(hostname, "2302", "/NEED_TO_ACTUALLY_MAKE_THIS"),
                "fa fa-tv", "mdl-color-text--purple-400"),
];
const services_me = [
    new Service("lumen", "Lumen",
                new URL(hostname, "2350", "/"),
                "fas fa-lightbulb", "mdl-color-text--yellow-600")
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

    // set up a card for third-party services my server is running
    const card_servs_tp = new Card("card_services_tp");
    card_servs_tp.set_title("Services");
    tab_overview.add_card(card_servs_tp);
    const card_servs_tp_desc = document.createElement("p");
    card_servs_tp_desc.innerHTML = "These third-party services are running " +
                                   "on the home server.";
    card_servs_tp.add_html(card_servs_tp_desc);
    init_service_card(card_servs_tp, services);

    // set up a card for homemade services my server is running
    const card_servs_me = new Card("card_services_me");
    card_servs_me.set_title("My Services");
    tab_overview.add_card(card_servs_me);
    const card_servs_me_desc = document.createElement("p");
    card_servs_me_desc.innerHTML = "These homemade services were created by " +
                                   "me for the home server.";
    card_servs_me.add_html(card_servs_me_desc);
    init_service_card(card_servs_me, services_me);

    // refresh the services
    refresh_services(services);
    refresh_services(services_me);
}

// Initializes the main card on the home page.
function init_card_main(card_main)
{
    // add introductory text to the card
    const p1 = document.createElement("p");
    p1.innerHTML = "This is my home server. It's under construction.";
    card_main.add_html(p1);
}

// Takes in a MDL card and a list of Service objects and initializes it to
// display each service.
function init_service_card(card, servs)
{
    // create a table to contain all services
    const table = document.createElement("table");
    table.className = "mdl-data-table mdl-js-data-table mdl-shadow--2dp " +
                      "mdl-color--grey-800";
    table.style.cssText = "width: 100%;";
    table.id = card.id + "_service_table";
    card.add_html(table);

    // add a header row to describe the entries
    const hrow = document.createElement("tr");
    const hcols = [
        ["", NaN],
        ["Name", NaN],
        ["Port", 0],
        ["Status", NaN]
    ];
    for (let i = 0; i < hcols.length; i++)
    {
        const th = document.createElement("th");
        th.className = "mdl-color-text--grey-200";
        if (isNaN(hcols[i][1]))
        { th.className += " mdl-data-table__cell--non-numeric"; }
        th.innerHTML = hcols[i][0];
        hrow.appendChild(th);
    }
    table.appendChild(hrow);

    // for each service, add a row to the table
    for (let i = 0; i < servs.length; i++)
    {
        const s = servs[i];
        const row = document.createElement("tr");

        // ping the service to determine if it's up, then create an appropriate
        // status icon
        status_icon = document.createElement("i");
        status_icon.className = "fas fa-hourglass mdl-color-text--grey-600";
        status_icon.id = s.id + "_status_icon";
        
        // construct an array of values to be stored in the row's columns
        const cols = [
            s.make_icon(),
            s.name,
            s.url.port,
            status_icon
        ];

        // for each column add a 'td' element
        for (let j = 0; j < cols.length; j++)
        {
            const td = document.createElement("td");
            const c = cols[j];
            if (isNaN(c))
            { td.className = "mdl-data-table__cell--non-numeric"; }

            // check the column's value and append accordingly
            if (c instanceof Element)
            { td.appendChild(c); }
            else
            { td.innerHTML = c; }
            row.appendChild(td);
        }
        table.appendChild(row);
    }
}

// Pings the list of given services and updates their status icons.
function refresh_services(servs)
{
    for (let i = 0; i < servs.length; i++)
    {
        const s = servs[i];

        // ping the service to determine if it's online or not
        const status_icon = document.getElementById(s.id + "_status_icon");
        online_css = "fas fa-check mdl-color-text--green-400";
        offline_css = "fas fa-xmark mdl-color-text--red-400";
        console.log(status_icon);
        s.ping().then(
            function(result)
            {
                if (result)
                { status_icon.className = online_css; }
                else
                { status_icon.className = offline_css; }
            },
            function()
            { status_icon.className = offline_css; }
        );
    }
}

