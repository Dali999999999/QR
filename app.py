import os
import logging
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
from mega import Mega
import uuid # Pour générer des noms de fichiers temporaires uniques

# Configuration du logging améliorée (pourrait être utile sur Render)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler() # Affiche les logs dans la sortie standard (visible sur Render)
    ]
)
# Obtenir un logger spécifique pour notre app (bonne pratique)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Configuration et Constantes ---
MEGA_EMAIL = os.environ.get('MEGA_EMAIL')
MEGA_PASSWORD = os.environ.get('MEGA_PASSWORD')
UPLOAD_FOLDER = '/tmp' # Dossier temporaire standard sur les systèmes Linux (comme Render)

# --- Vérifications Initiales ---
if not MEGA_EMAIL or not MEGA_PASSWORD:
    logger.critical("ERREUR CRITIQUE: Les variables d'environnement MEGA_EMAIL et MEGA_PASSWORD doivent être définies.")
    # On pourrait vouloir empêcher Flask de démarrer ici, mais pour l'instant on log

# Création du dossier temporaire si nécessaire
if not os.path.exists(UPLOAD_FOLDER):
     try:
         os.makedirs(UPLOAD_FOLDER)
         logger.info(f"Dossier temporaire créé: {UPLOAD_FOLDER}")
     except OSError as e:
         logger.error(f"Impossible de créer le dossier temporaire {UPLOAD_FOLDER}: {e}")
         # Gérer l'erreur si nécessaire, par exemple en arrêtant l'application

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- Fonctions Utilitaires ---
def get_mega_instance():
    """Initialise et retourne une instance Mega connectée."""
    if not MEGA_EMAIL or not MEGA_PASSWORD:
        logger.error("Tentative de connexion à Mega échouée : Identifiants non configurés.")
        return None
    try:
        # N'affiche que les 4 premiers caractères de l'email dans les logs
        logger.info(f"Tentative de connexion à Mega avec l'email : {MEGA_EMAIL[:4]}...")
        mega = Mega()
        m = mega.login(MEGA_EMAIL, MEGA_PASSWORD)
        logger.info("Connexion à Mega réussie.")
        return m
    except Exception as e:
        logger.error(f"Échec de la connexion à Mega: {e}", exc_info=True) # exc_info=True pour la trace d'erreur
        return None

# --- Routes Flask ---
@app.route('/')
def index():
    """Route de base pour vérifier si le service est opérationnel."""
    return jsonify({"message": "Le backend de génération de QR Code Image est opérationnel!"})

@app.route('/upload', methods=['POST'])
def upload_image():
    """Route pour recevoir une image, l'uploader sur Mega et retourner le lien."""
    logger.info("Requête reçue sur /upload")

    # 1. Vérification de la requête
    if 'file' not in request.files:
        logger.warning("Aucun fichier trouvé dans la requête (clé 'file' manquante).")
        return jsonify({"error": "Aucun fichier fourni (champ 'file' manquant)"}), 400

    file = request.files['file']

    if file.filename == '':
        logger.warning("Nom de fichier vide reçu.")
        return jsonify({"error": "Nom de fichier vide"}), 400

    if file:
        # 2. Sauvegarde temporaire sécurisée
        original_filename = secure_filename(file.filename)
        # Génère un nom de fichier unique pour éviter les conflits dans /tmp
        temp_filename = f"{uuid.uuid4()}_{original_filename}"
        temp_filepath = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)

        logger.info(f"Réception du fichier: '{original_filename}'. Sauvegarde temporaire sous: '{temp_filepath}'")

        try:
            file.save(temp_filepath)
            logger.info(f"Fichier sauvegardé temporairement: '{temp_filepath}'")

            # 3. Connexion à Mega
            m = get_mega_instance()
            if m is None:
                 logger.error("Échec de l'obtention de l'instance Mega pour l'upload.")
                 # Le fichier temporaire sera nettoyé dans le 'finally'
                 return jsonify({"error": "Échec de la connexion au service de stockage"}), 503

            # 4. Téléversement sur Mega
            logger.info(f"Téléversement du fichier '{temp_filename}' ({os.path.getsize(temp_filepath)} bytes) sur Mega...")
            uploaded_file_node = m.upload(temp_filepath)
            logger.info(f"Fichier '{temp_filename}' téléversé sur Mega.")

            # ---> POINT CRUCIAL : Inspection et Gestion du Handle <---
            logger.info(f"Structure retournée par m.upload: {uploaded_file_node}") # Log pour inspection !

            # Utilise .get() pour accéder à la clé 'h' de manière sécurisée
            file_handle = uploaded_file_node.get('h')

            if file_handle:
                logger.info(f"Handle du fichier trouvé (clé 'h'): {file_handle}")

                # 5. Obtenir le lien public
                try:
                    public_link = m.export(file_handle)
                    logger.info(f"Lien public Mega généré avec succès.")
                    # 6. Retourner le lien
                    return jsonify({"url": public_link}), 200 # Code 200 explicite pour succès

                except Exception as export_error:
                    # Erreur spécifique lors de la création du lien public
                    logger.error(f"Erreur lors de l'exportation du lien Mega pour le handle {file_handle}: {export_error}", exc_info=True)
                    return jsonify({"error": f"Erreur interne lors de la création du lien public: {export_error}"}), 500

            else:
                # Si la clé 'h' n'est pas trouvée dans la réponse de m.upload()
                logger.error(f"La clé 'h' (handle) est manquante dans la réponse de m.upload()! Réponse complète: {uploaded_file_node}")
                return jsonify({"error": "Erreur interne: Impossible de récupérer l'identifiant du fichier après upload."}), 500
            # ---> FIN DU POINT CRUCIAL <---

        except Exception as e:
            # Capture toute autre exception pendant le processus
            logger.error(f"Erreur globale lors du traitement du fichier '{original_filename}': {e}", exc_info=True)
            # Retourne une erreur générique 500 mais l'erreur spécifique est logguée sur le serveur
            return jsonify({"error": "Erreur interne du serveur lors du traitement du fichier."}), 500

        finally:
            # 7. Nettoyage : Supprimer le fichier temporaire DANS TOUS LES CAS
            if os.path.exists(temp_filepath):
                try:
                    os.remove(temp_filepath)
                    logger.info(f"Fichier temporaire supprimé: '{temp_filepath}'")
                except OSError as e_remove:
                    logger.error(f"Erreur lors de la suppression du fichier temporaire '{temp_filepath}': {e_remove}")
    else:
        # Ce cas ne devrait pas arriver si 'file' in request.files est vrai, mais par sécurité
        logger.warning("Logique inattendue : 'file' est évalué comme False après vérifications initiales.")
        return jsonify({"error": "Fichier invalide ou non traité"}), 400

# --- Démarrage (pour Gunicorn sur Render) ---
# Pas besoin de app.run() ici, Gunicorn s'en charge via le Procfile.

# --- Bloc pour test local (Optionnel) ---
# if __name__ == '__main__':
#    logger.info("Démarrage du serveur Flask pour test local.")
#    # ATTENTION : Ne pas utiliser debug=True en production !
#    # Assurez-vous que les variables d'environnement MEGA_EMAIL et MEGA_PASSWORD sont définies
#    # dans votre terminal avant de lancer :
#    # export MEGA_EMAIL='votre_email@mega.com'
#    # export MEGA_PASSWORD='votremotdepasse'
#    # python app.py
#    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False)
