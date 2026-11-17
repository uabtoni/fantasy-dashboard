# Fantasy Dashboard · Panel de mercado de LaLiga Fantasy

Panel web que muestra los datos que recoge la plataforma **[fantasy](https://github.com/uabtoni/fantasy)**: mercado, comparador de jugadores, mi equipo y finanzas de la liga privada.

Se despliega automáticamente en **GitHub Pages** con cada `push` a `main`.

## Funcionalidades

- **Mercado:** precio, posición y estado físico (disponible, duda o lesionado) de todos los jugadores, con buscador y filtros.
- **Comparador:** varios jugadores enfrentados lado a lado.
- **Mi equipo:** vista de campo con validación de formación.
- **Mercado de mi liga:** jugadores que hay ahora mismo a la venta en la liga privada.
- **Finanzas de la liga:** saldo y margen de endeudamiento de cada manager, calculado a partir del historial de fichajes.

## Aspectos técnicos

- **Sin servidor propio:** es una página estática (HTML, CSS y JavaScript) que consulta directamente la API de Supabase desde el navegador.
- **Seguridad de claves:** la página nunca lleva credenciales en el código. Cada usuario introduce la URL y la **clave pública (anon)** de su proyecto, que se guarda solo en su navegador (`localStorage`). La propia interfaz advierte que no se use nunca la clave `service_role`. El acceso a los datos lo controlan las políticas de seguridad de fila (RLS) de Supabase.
- **Despliegue continuo:** el workflow `.github/workflows/deploy-pages.yml` publica la web en GitHub Pages con permisos mínimos (`contents: read`, `pages: write`) y cancela los despliegues anteriores si llega uno nuevo.

## Tecnologías

HTML · CSS · JavaScript · Supabase (API REST) · GitHub Actions · GitHub Pages

## Uso

1. Abre la web publicada en GitHub Pages.
2. Introduce la URL de tu proyecto de Supabase y su clave **anon/public**.
3. Pulsa **Conectar**.

---

Proyecto personal de [Toni Susany](https://github.com/uabtoni).
