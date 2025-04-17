import os
import logging
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
from mega import Mega
from mega.errors import RequestError, ValidationError # Ajout de RequestError
import uuid
# import time # Plus nécessaire car on enlève le délai explicite

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
    # Dans un cas réel, on pourrait vouloir arrêter l'application ici

if not os.path.exists(UPLOAD_FOLDER):
     try:
         os.makedirs(UPLOAD_FOLDER)
         logger.info(f"Dossier temporaire créé: {UPLOAD_FOLDER}")
     except OSError as e:
         logger.error(f"Impossible de créer le dossier temporaire {UPLOAD_FOLDER}: {e}")
         # Gérer l'erreur si nécessaire

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- Fonctions Utilitaires ---
def get_mega_instance():
    """Initialise et retourne une instance Mega connectée."""
    if not MEGA_EMAIL or not MEGA_PASSWORD:
        logger.error("Tentative de connexion à Mega échouée : Identifiants non configurés.")
        return None
    try:
        logger.info(f"Tentative de connexion à Mega avec l'email : {MEGA_EMAIL[:4]}...") # Ne pas logger l'email complet
        mega = Mega()
        m = mega.login(MEGA_EMAIL, MEGA_PASSWORD)
        logger.info("Connexion à Mega réussie.")
        return m
    except RequestError as req_err:
        logger.error(f"Échec de la connexion à Mega (RequestError {req_err.code}): {req_err}")
        return None
    except Exception as e:
        logger.error(f"Échec de la connexion à Mega (Erreur générale): {e}", exc_info=True) # exc_info pour la stack trace complète
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
        # Générer un nom de fichier unique pour éviter les collisions dans /tmp
        temp_filename = f"{uuid.uuid4()}_{original_filename}"
        temp_filepath = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)

        logger.info(f"Réception du fichier: '{original_filename}'. Sauvegarde temporaire sous: '{temp_filepath}'")

        try:
            file.save(temp_filepath)
            file_size = os.path.getsize(temp_filepath) # Taille après sauvegarde
            logger.info(f"Fichier sauvegardé temporairement: '{temp_filepath}' (Taille: {file_size} bytes)")

            # 3. Connexion à Mega
            m = get_mega_instance()
            if m is None:
                 logger.error("Échec de l'obtention de l'instance Mega pour l'upload.")
                 # Nettoyer le fichier temporaire avant de retourner l'erreur
                 if os.path.exists(temp_filepath):
                     try:
                         os.remove(temp_filepath)
                         logger.info(f"Fichier temporaire '{temp_filepath}' supprimé après échec de connexion Mega.")
                     except OSError as e_remove:
                         logger.error(f"Erreur lors de la suppression du fichier temp après échec Mega: {e_remove}")
                 return jsonify({"error": "Échec de la connexion au service de stockage"}), 503 # Service Unavailable

            # 4. Téléversement sur Mega
            logger.info(f"Téléversement du fichier '{temp_filename}' ({file_size} bytes) sur Mega...")
            uploaded_file_node_response = m.upload(temp_filepath)
            logger.info(f"Fichier '{temp_filename}' téléversé sur Mega.")
            logger.info(f"Structure retournée par m.upload: {uploaded_file_node_response}") # Utile pour le débogage

            # ====> DÉBUT DE LA SECTION CORRIGÉE <====

            # 5. Obtenir le lien public DIRECTEMENT depuis la réponse de l'upload
            #    Pas besoin de refaire un find() qui peut échouer à cause de la latence.
            public_link = None
            try:
                # La fonction get_upload_link est conçue pour ça !
                # Elle prend en argument la réponse de m.upload()
                logger.info("Tentative de génération du lien via m.get_upload_link()...")
                public_link = m.get_upload_link(uploaded_file_node_response)
                logger.info(f"Lien public Mega généré (via get_upload_link): {public_link}")

            except Exception as e_get_link:
                logger.error(f"Erreur lors de la génération du lien public avec get_upload_link: {e_get_link}", exc_info=True)
                # Tentative de fallback avec m.export() si get_upload_link échoue
                logger.warning("get_upload_link a échoué. Tentative de fallback avec m.export()...")
                try:
                    # Essayer d'extraire le handle 'h' de la réponse pour m.export()
                    file_handle = None
                    f_list = uploaded_file_node_response.get('f')
                    if isinstance(f_list, list) and f_list:
                        first_element = f_list[0]
                        if isinstance(first_element, dict):
                            file_handle = first_element.get('h')

                    if file_handle:
                         logger.info(f"Handle '{file_handle}' trouvé pour le fallback m.export().")
                         public_link = m.export(file_handle) # m.export nécessite juste le handle
                         logger.info(f"Lien public Mega généré (via m.export fallback): {public_link}")
                    else:
                         logger.error("Handle non trouvé dans la réponse d'upload pour le fallback m.export(). Impossible de continuer.")
                         raise ValueError("Handle non trouvé pour le fallback export.") # Lève une erreur pour le except externe

                except Exception as inner_e:
                     # Si le fallback échoue aussi
                     logger.error(f"Le fallback m.export a aussi échoué: {inner_e}", exc_info=True)
                     # On renvoie l'erreur initiale de get_upload_link ou du fallback si elle est plus pertinente
                     error_message = f"Erreur interne lors de la génération du lien post-upload ({type(e_get_link).__name__} / {type(inner_e).__name__})."
                     return jsonify({"error": error_message}), 500

            # 6. Retourner le lien à l'application Flutter / au script de test si obtenu
            if public_link:
                 return jsonify({"url": public_link}), 200
            else:
                 # Ce cas ne devrait pas arriver si les try/except ci-dessus sont corrects, mais par sécurité
                 logger.error("Le lien public est resté None après toutes les tentatives. Erreur inattendue.")
                 return jsonify({"error": "Erreur interne inattendue lors de la finalisation du lien public."}), 500

            # ====> FIN DE LA SECTION CORRIGÉE <====

        except Exception as e:
            # Capture les erreurs majeures pendant le processus (sauvegarde, upload, etc.)
            logger.error(f"Erreur majeure lors du traitement du fichier '{original_filename}': {e}", exc_info=True)
            return jsonify({"error": f"Erreur interne majeure du serveur ({type(e).__name__})."}), 500

        finally:
            # 7. Nettoyage du fichier temporaire (TRES IMPORTANT)
            # Ce bloc s'exécute TOUJOURS, que le try réussisse ou échoue.
            if os.path.exists(temp_filepath):
                try:
                    os.remove(temp_filepath)
                    logger.info(f"Fichier temporaire supprimé: '{temp_filepath}'")
                except OSError as e_remove:
                    logger.error(f"Erreur lors de la suppression du fichier temporaire '{temp_filepath}': {e_remove}")
    else:
        # Ce cas ne devrait théoriquement pas arriver si les vérifs initiales sont bonnes
        logger.warning("Logique inattendue : 'file' est évalué comme False après vérifications initiales.")
        return jsonify({"error": "Fichier invalide ou non traité"}), 400

# --- Démarrage (pour Gunicorn sur Render) ---
# Le serveur WSGI (comme Gunicorn) importe 'app' et l'exécute.
# Le bloc if __name__ == '__main__': n'est PAS utilisé par Gunicorn.
# Laisser ce bloc pour les tests locaux si nécessaire.

# if __name__ == '__main__':
#    # ATTENTION : Ne pas utiliser debug=True en production !
#    # Pour tester localement, définir les variables d'environnement :
#    # export MEGA_EMAIL='ton_email@example.com'
#    # export MEGA_PASSWORD='ton_mot_de_passe'
#    # python app.py
#    # Render définit la variable PORT automatiquement.
#    port = int(os.environ.get('PORT', 8080)) # Utilise 8080 par défaut localement
#    # Écoute sur 0.0.0.0 pour être accessible depuis l'extérieur du conteneur/VM
#    app.run(host='0.0.0.0', port=port, debug=False)
