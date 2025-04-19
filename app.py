import os
import logging
from flask import Flask, request, jsonify, send_file, make_response
from werkzeug.utils import secure_filename
from mega import Mega
from mega.errors import RequestError, ValidationError
import uuid
import io  # Pour manipuler les données en mémoire si nécessaire (non utilisé ici pour le dl)
import mimetypes # Pour deviner le type de fichier

# Configuration du logging améliorée
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler() # Afficher les logs dans la console/Render
    ]
)
logger = logging.getLogger(__name__) # Utiliser le logger nommé 'app'

app = Flask(__name__)

# --- Configuration et Constantes ---
MEGA_EMAIL = os.environ.get('MEGA_EMAIL')
MEGA_PASSWORD = os.environ.get('MEGA_PASSWORD')
UPLOAD_FOLDER = '/tmp' # Dossier temporaire standard sur les systèmes type Unix (comme Render)

# --- Vérifications Initiales ---
if not MEGA_EMAIL or not MEGA_PASSWORD:
    logger.critical("ERREUR CRITIQUE: Les variables d'environnement MEGA_EMAIL et MEGA_PASSWORD doivent être définies.")

if not os.path.exists(UPLOAD_FOLDER):
     try:
         os.makedirs(UPLOAD_FOLDER)
         logger.info(f"Dossier temporaire créé: {UPLOAD_FOLDER}")
     except OSError as e:
         logger.error(f"Impossible de créer le dossier temporaire {UPLOAD_FOLDER}: {e}")

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- Fonctions Utilitaires ---
def get_mega_instance():
    """Initialise et retourne une instance Mega connectée AVEC COMPTE."""
    # Utilisé pour l'upload
    if not MEGA_EMAIL or not MEGA_PASSWORD:
        logger.error("Tentative de connexion à Mega (compte) échouée : Identifiants non configurés.")
        return None
    try:
        logger.info(f"Tentative de connexion à Mega (compte) avec l'email : {MEGA_EMAIL[:4]}...")
        mega = Mega()
        m = mega.login(MEGA_EMAIL, MEGA_PASSWORD)
        logger.info("Connexion à Mega (compte) réussie.")
        return m
    except RequestError as req_err:
        logger.error(f"Échec de la connexion à Mega (compte - RequestError {req_err.code}): {req_err}")
        return None
    except Exception as e:
        logger.error(f"Échec de la connexion à Mega (compte - Erreur générale): {e}", exc_info=True)
        return None

# --- Routes Flask ---
@app.route('/')
def index():
    """Route de base pour vérifier si le service est opérationnel."""
    return jsonify({"message": "Le backend QR Code Image (avec proxy Mega) est opérationnel!"})

# --- ROUTE /upload (INCHANGÉE DANS SA LOGIQUE DE BASE) ---
# Elle reçoit une image, l'upload et retourne le lien public Mega
@app.route('/upload', methods=['POST'])
def upload_image():
    """
    Reçoit une image, l'upload sur le compte Mega de l'admin
    et retourne le LIEN PUBLIC Mega.
    """
    logger.info("Requête reçue sur /upload")
    # ... (Le reste de la logique de validation et d'upload est identique à la version précédente) ...
    # ... (Vérification 'file', sauvegarde temporaire, get_mega_instance(), m.upload()) ...

    # --- Partie spécifique à upload ---
    if 'file' not in request.files:
        logger.warning("Upload: Aucun fichier trouvé.")
        return jsonify({"error": "Aucun fichier fourni"}), 400
    file = request.files['file']
    if file.filename == '':
        logger.warning("Upload: Nom de fichier vide.")
        return jsonify({"error": "Nom de fichier vide"}), 400

    if file:
        original_filename = secure_filename(file.filename)
        temp_filename_upload = f"{uuid.uuid4()}_UPLOAD_{original_filename}"
        temp_filepath_upload = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename_upload)
        logger.info(f"Upload: Sauvegarde temporaire sous: '{temp_filepath_upload}'")

        try:
            file.save(temp_filepath_upload)
            file_size = os.path.getsize(temp_filepath_upload)
            logger.info(f"Upload: Fichier sauvegardé: '{temp_filepath_upload}' ({file_size} bytes)")

            # Connexion avec le compte principal
            m = get_mega_instance()
            if m is None:
                 logger.error("Upload: Échec connexion Mega.")
                 return jsonify({"error": "Échec connexion service stockage"}), 503

            logger.info(f"Upload: Téléversement '{temp_filename_upload}' sur Mega...")
            uploaded_file_node_response = m.upload(temp_filepath_upload)
            logger.info(f"Upload: Téléversement terminé.")
            logger.debug(f"Upload: Réponse m.upload: {uploaded_file_node_response}") # Debug au lieu d'info

            # Génération du lien public
            public_link = None
            try:
                logger.info("Upload: Génération lien via m.get_upload_link()...")
                public_link = m.get_upload_link(uploaded_file_node_response)
                logger.info(f"Upload: Lien public généré: {public_link}")
            except Exception as e_get_link:
                logger.error(f"Upload: Erreur get_upload_link: {e_get_link}", exc_info=True)
                # Fallback avec m.export()
                logger.warning("Upload: Fallback avec m.export()...")
                try:
                    file_handle = uploaded_file_node_response.get('f', [{}])[0].get('h')
                    if file_handle:
                         public_link = m.export(file_handle)
                         logger.info(f"Upload: Lien public généré (fallback): {public_link}")
                    else: raise ValueError("Handle non trouvé pour fallback.")
                except Exception as inner_e:
                     logger.error(f"Upload: Fallback export échoué: {inner_e}", exc_info=True)
                     return jsonify({"error": "Erreur interne génération lien post-upload"}), 500

            if public_link:
                 # C'est CE lien public qui sera dans le QR code
                 return jsonify({"url": public_link}), 200
            else:
                 logger.error("Upload: Lien public final est None.")
                 return jsonify({"error": "Erreur interne finalisation lien public."}), 500

        except Exception as e:
            logger.error(f"Upload: Erreur majeure '{original_filename}': {e}", exc_info=True)
            return jsonify({"error": f"Erreur interne serveur ({type(e).__name__})."}), 500
        finally:
            if os.path.exists(temp_filepath_upload):
                try:
                    os.remove(temp_filepath_upload)
                    logger.info(f"Upload: Fichier temporaire supprimé: '{temp_filepath_upload}'")
                except OSError as e_remove:
                    logger.error(f"Upload: Erreur suppression temp: {e_remove}")
    else:
        return jsonify({"error": "Fichier invalide ou non traité"}), 400


# --- NOUVELLE ROUTE : /get_image_from_mega_link ---
@app.route('/get_image_from_mega_link', methods=['POST'])
def get_image_from_mega_link():
    """
    Reçoit un lien public Mega, télécharge le fichier correspondant sur le serveur,
    puis retourne les octets bruts de l'image déchiffrée.
    """
    logger.info("Requête reçue sur /get_image_from_mega_link")

    # 1. Récupérer le lien depuis le corps JSON de la requête
    data = request.get_json()
    if not data:
        logger.warning("DownloadProxy: Corps de requête vide ou non JSON.")
        return jsonify({"error": "Corps de requête JSON manquant ou invalide"}), 400

    mega_url = data.get('mega_url')
    if not mega_url:
        logger.warning("DownloadProxy: Clé 'mega_url' manquante dans JSON.")
        return jsonify({"error": "Clé 'mega_url' manquante"}), 400

    # 2. Valider basiquement le format de l'URL (optionnel mais recommandé)
    if not mega_url.startswith("https://mega.") or '!' not in mega_url:
         logger.warning(f"DownloadProxy: Format URL Mega invalide reçu: {mega_url}")
         return jsonify({"error": "QR code invalide"}), 400

    logger.info(f"DownloadProxy: Traitement du lien: {mega_url[:35]}...") # Log tronqué

    # 3. Initialiser Mega (peut se faire anonymement pour télécharger un lien public)
    try:
        logger.info("DownloadProxy: Initialisation session Mega anonyme...")
        # Note: Pas besoin de login compte admin ici, on télécharge un lien public
        mega_downloader = Mega()
        # Tenter un login anonyme pour établir une session, même si pas strictement nécessaire pour l'API 'g'
        # m_anon = mega_downloader.login_anonymous() # Optionnel, download_url le gère peut-être
        logger.info("DownloadProxy: Session Mega initialisée.")
    except Exception as e_init:
        logger.error(f"DownloadProxy: Erreur initialisation Mega anonyme: {e_init}", exc_info=True)
        return jsonify({"error": "Erreur interne initialisation service"}), 500

    # 4. Télécharger le fichier depuis le lien public SUR LE SERVEUR RENDER
    temp_download_path = None # Garder une trace pour le cleanup
    try:
        logger.info(f"DownloadProxy: Tentative de téléchargement depuis l'URL Mega...")
        # m.download_url() télécharge ET déchiffre le fichier dans le dossier spécifié.
        # Il retourne le chemin complet du fichier téléchargé sur le serveur.
        # Le nom de fichier sera celui stocké dans les métadonnées Mega.
        downloaded_filepath = mega_downloader.download_url(
            url=mega_url,
            dest_path=app.config['UPLOAD_FOLDER']
            # Possibilité d'ajouter dest_filename si on veut forcer un nom temporaire
        )
        temp_download_path = str(downloaded_filepath) # Conserve le chemin pour finally
        logger.info(f"DownloadProxy: Fichier téléchargé et déchiffré sur le serveur : '{temp_download_path}'")

        # 5. Envoyer les données du fichier téléchargé au client
        if not os.path.exists(temp_download_path):
             logger.error(f"DownloadProxy: Fichier téléchargé non trouvé sur disque à '{temp_download_path}' après download_url !")
             return jsonify({"error": "Erreur interne: Fichier disparu après téléchargement"}), 500

        # Déterminer le mimetype
        mimetype, _ = mimetypes.guess_type(temp_download_path)
        if not mimetype or not mimetype.startswith('image/'):
            logger.warning(f"DownloadProxy: Impossible de déterminer un mimetype d'image valide pour '{temp_download_path}'. Mimetype deviné: {mimetype}. Utilisation de 'application/octet-stream'.")
            mimetype = 'application/octet-stream' # Fallback générique

        logger.info(f"DownloadProxy: Envoi du fichier '{os.path.basename(temp_download_path)}' avec mimetype '{mimetype}'...")

        # Utiliser send_file pour streamer le fichier depuis le disque du serveur vers le client
        response = make_response(send_file(
            temp_download_path,
            mimetype=mimetype,
            as_attachment=False # Important pour que le client essaie de l'afficher
        ))
        # Optionnel: Ajouter des headers pour le cache si besoin
        # response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        # response.headers['Pragma'] = 'no-cache'
        # response.headers['Expires'] = '0'
        return response

    except RequestError as req_err:
        logger.error(f"DownloadProxy: Erreur Mega API lors du téléchargement ({req_err.code}): {req_err}", exc_info=True)
        status_code = 502 # Bad Gateway (erreur en amont)
        error_msg = f"Erreur du service de stockage ({req_err.code})"
        if req_err.code == -9: # Fichier non trouvé
             status_code = 404
             error_msg = "Fichier non trouvé sur le service de stockage (lien invalide ou expiré?)"
        elif req_err.code == -2: # Argument invalide
              status_code = 400
              error_msg = "Format de lien Mega incorrect"
        return jsonify({"error": error_msg, "details": str(req_err)}), status_code
    except Exception as e:
        logger.error(f"DownloadProxy: Erreur inattendue lors du téléchargement/envoi: {e}", exc_info=True)
        return jsonify({"error": f"Erreur interne du serveur ({type(e).__name__})."}), 500
    finally:
        # 6. Nettoyer le fichier téléchargé sur le serveur (TRÈS IMPORTANT)
        if temp_download_path and os.path.exists(temp_download_path):
            try:
                os.remove(temp_download_path)
                logger.info(f"DownloadProxy: Fichier temporaire serveur supprimé: '{temp_download_path}'")
            except OSError as e_remove:
                logger.error(f"DownloadProxy: Erreur suppression temp serveur '{temp_download_path}': {e_remove}")

# --- Démarrage (pour Gunicorn sur Render) ---
# (Laisser commenté pour la production sur Render)
# if __name__ == '__main__':
#    port = int(os.environ.get('PORT', 8080))
#    app.run(host='0.0.0.0', port=port, debug=False)
