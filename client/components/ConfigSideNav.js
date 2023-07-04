"use client";
import React, {useState} from "react";
import {Button, Form, Spinner, Stack} from "react-bootstrap";
import {toast} from "react-toastify";

export default function ConfigSideNav({onUser, onModel}) {
    const [isLoading, setIsLoading] = useState(false);
    const [currentUserId, setCurrentUserId] = useState("None");
    const [currentModelName, setCurrentModelName] = useState("None");
    const [downloadInProgress, setdownloadInProgress] = useState(false);
    const [selectedFile, setSelectedFile] = useState(null);
    const [isUploading, setIsUploading] = useState(null);

    const ingestData = async () => {
        try {
            setIsLoading(true);
            const res = await fetch("http://localhost:8888/ingest");
            const jsonData = await res.json();
            if (!res.ok) {
                // This will activate the closest `error.js` Error Boundary
                console.log("Error Ingesting data");
                toast.error("Error Ingesting data.");
                setIsLoading(false);
            } else {
                setIsLoading(false);
                res.text().then(text => {
                    toast.success("Successfully indexed data." + text);
                })
                console.log(jsonData);
            }
        } catch (error) {
            setIsLoading(false);
            res.text().then(text => {
                toast.error("Error Ingesting data." + text);
            })
        }
    };

    const handleUserChange = async (event) => {
        const userId = event.target.value;
        if (userId !== currentUserId) {
            console.log("User changed to " + userId);
            setCurrentUserId(userId);
            onUser(userId);
        }
    }
    const handleModelChange = async (event) => {
        const modelName = event.target.value;
        if (modelName !== currentModelName) {
            console.log("Model changed to " + modelName);
            setCurrentModelName(modelName);
            onModel(modelName);
        }
    }

    const handleDownloadModel = async () => {
        try {
            setdownloadInProgress(true);
            const res = await fetch("http://localhost:8888/download_model");
            const jsonData = await res.json();
            if (!res.ok) {
                response.text().then(text => {
                    toast.error("Error downloading model." + text);
                })
                setdownloadInProgress(false);
            } else {
                setdownloadInProgress(false);
                toast.success("Model Download complete");
                console.log(jsonData);
            }
        } catch (error) {
            setdownloadInProgress(false);
            console.log(error);
            toast.error("Error downloading model");
        }
    };

    const handleFileChange = (event) => {
        if (event.target.files[0] != null) {
            setSelectedFile(event.target.files[0]);
        }

    };

    const handleUpload = async () => {
        setIsUploading(true)
        try {
            const formData = new FormData();
            formData.append("document", selectedFile);

            const res = await fetch("http://localhost:8888/upload_doc", {
                method: "POST",
                body: formData,
            });

            if (!res.ok) {
                console.log("Error Uploading document");
                response.text().then(text => {
                    toast.error("Error Uploading document." + text);
                })
                setSelectedFile(null); // Clear the selected file after successful upload
                document.getElementById("file-input").value = "";
                setIsUploading(false)
            } else {
                const data = await res.json();
                console.log(data);
                toast.success("Document Upload Successful");
                setSelectedFile(null); // Clear the selected file after successful upload
                document.getElementById("file-input").value = "";
                setIsUploading(false)
            }
        } catch (error) {
            console.log("error");
            toast.error("Error Uploading document");
            setSelectedFile(null); // Clear the selected file after successful upload
            document.getElementById("file-input").value = "";
            setIsUploading(false)
        }
    };

    return (
        <>
            <div className="mx-4 mt-3">
                <Form.Group className="mb-3">
                    <Form.Label>Select User</Form.Label>
                    <Form.Select aria-label="user-select" onChange={handleUserChange}>
                        <option value="None">Select a user...</option>
                        <option value="Ken">Ken (CEO)</option>
                        <option value="Jeff">Jeff (COO)</option>
                        <option value="Andrew">Andrew (CFO)</option>
                    </Form.Select>
                </Form.Group>
            </div>
            <div className="mx-4 mt-3">
                <Form.Group className="mb-3">
                    <Form.Label>Select Model</Form.Label>
                    <Form.Select aria-label="user-select" onChange={handleModelChange}>
                        <option value="None">Select a model...</option>
                        <option value="Falcon-40B">Falcon-40B</option>
                        <option value="Swiss-Finish">Swiss-Finish</option>
                        <option value="GPT-3.5-Turbo">GPT-3.5-Turbo</option>
                    </Form.Select>
                </Form.Group>
            </div>

            <div className="mx-4 mt-3">
                <Form.Group className="mb-3">
                    <Form.Label>Upload your documents</Form.Label>
                    <Form.Control
                        type="file"
                        size="sm"
                        onChange={handleFileChange}
                        id="file-input"
                        disabled={true}
                    />
                </Form.Group>
                <Stack direction="horizontal" className="mt-3" gap={3}>
                    {isUploading ? <div className="d-flex justify-content-center"><Spinner animation="border"/><span
                            className="ms-3">uploading</span></div> :
                        <Button disabled={true} onClick={(e) => handleUpload()}>Upload</Button>}
                    {isLoading ? (
                        <div className="d-flex justify-content-center"><Spinner animation="border"/><span
                            className="ms-3">ingesting</span></div>
                    ) : (
                        <Button onClick={() => ingestData()}>Create Index</Button>
                    )}
                </Stack>
            </div>
        </>
    );
}
